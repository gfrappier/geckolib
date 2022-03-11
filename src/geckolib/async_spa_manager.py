"""Class to manage the clienting of a Gecko in.touch2 enabled device """
from __future__ import annotations

from abc import ABC, abstractmethod
import asyncio

from .automation import GeckoAsyncFacade
from .async_locator import GeckoAsyncLocator
from .async_spa import GeckoAsyncSpa
from .async_spa_descriptor import GeckoAsyncSpaDescriptor
from .async_tasks import AsyncTasks
from .const import GeckoConstants
from .spa_events import GeckoSpaEvent
from .spa_state import GeckoSpaState

import logging
from typing import Optional, List

_LOGGER = logging.getLogger(__name__)


class GeckoAsyncSpaMan(ABC, AsyncTasks):
    """GeckoAsyncSpaMan class manages the lifetime of a connection to a spa

    This class is deliberately an abstract because you must provide your own
    implementation to manage the essential events that are required during operation
    """

    def __init__(self, client_uuid: str, **kwargs: str) -> None:
        """Initialize a SpaMan class

        The preferred pattern is to derive a class from SpaMan and then use it in
        an async with

        ```async with MySpaMan(client_uuid, **kwargs) as SpaMan:```

        If you leave **kwargs empty, then you are responsible for co-ordinating calls
        to methods such as async_locate_spas, async_connect_to_spa and async_connect.

        **kwargs can contain the following items

            spa_address:    The IP address of the spa. Useful if the spa is on a sub-net
            spa_identifier: The ID of a spa (as a string)
            spa_name:       The name of the spa. Useful for status feedback when spa
                            cannot be contacted

        If any of the **kwargs are provided then the spam manager will automatically
        run the sequence discover and connect
        """
        AsyncTasks.__init__(self)
        self._client_id = GeckoConstants.FORMAT_CLIENT_IDENTIFIER.format(
            client_uuid
        ).encode(GeckoConstants.MESSAGE_ENCODING)

        # Optional parameters as supplied from config
        self._spa_address: Optional[str] = kwargs.get("spa_address", None)
        if self._spa_address == "":
            self._spa_address = None
        self._spa_identifier: Optional[str] = kwargs.get("spa_identifier", None)
        if self._spa_identifier == "":
            self._spa_identifier = None
        self._spa_name: Optional[str] = kwargs.get("spa_name", None)
        if self._spa_name == "":
            self._spa_name = None

        self._spa_descriptors: Optional[List[GeckoAsyncSpaDescriptor]] = None
        self._facade: Optional[GeckoAsyncFacade] = None
        self._spa: Optional[GeckoAsyncSpa] = None
        self._spa_state = GeckoSpaState.IDLE

    ########################################################################
    #
    #   Usage helpers
    #

    async def __aenter__(self) -> GeckoAsyncSpaMan:
        await AsyncTasks.__aenter__(self)
        await self._handle_event(GeckoSpaEvent.SPA_MAN_ENTER)
        self.add_task(self._sequence_pump(), "Sequence Pump", "SPAMAN")
        return self

    async def __aexit__(self, *exc_info) -> None:
        self.cancel_key_tasks("SPAMAN")
        await self._handle_event(GeckoSpaEvent.SPA_MAN_EXIT, exc_info=exc_info)
        await AsyncTasks.__aexit__(self, exc_info)

    ########################################################################
    #
    #   Public methods
    #

    async def async_reset(self) -> None:
        """Reset the spa manager"""
        self._spa_descriptors = None
        if self._facade is not None:
            self._facade = None
            # self._handle_event(GeckoSpaEvent.)
        if self._spa is not None:
            await self._spa.disconnect()
            self._spa = None
        self._spa_state = GeckoSpaState.IDLE

    async def async_locate_spas(
        self, spa_address: Optional[str] = None, spa_identifier: Optional[str] = None
    ) -> Optional[List[GeckoAsyncSpaDescriptor]]:
        """Locate spas on this network

        This API will return a list of GeckoAsyncSpaDescriptor that were
        found on the network. If there are none found, then the return will be
        None. Events will be issued as the locating process proceeds


        """
        try:
            await self._handle_event(GeckoSpaEvent.LOCATING_STARTED)
            locator = GeckoAsyncLocator(
                self,
                self._handle_event,
                spa_address=spa_address,
                spa_identifier=spa_identifier,
            )
            await locator.discover()
            self._spa_descriptors = locator.spas
            del locator

        finally:
            await self._handle_event(
                GeckoSpaEvent.LOCATING_FINISHED,
                spa_descriptors=self._spa_descriptors,
            )

        return self._spa_descriptors

    async def async_connect_to_spa(self, spa_descriptor) -> Optional[GeckoAsyncFacade]:
        """Connect to spa.

        This API will connect to the specified spa using the supplied descriptor"""
        assert self._facade is None

        try:
            await self._handle_event(GeckoSpaEvent.CONNECTION_STARTED)
            self._spa = GeckoAsyncSpa(
                self._client_id, spa_descriptor, self, self._handle_event
            )
            await self._spa.connect()
            # Check state now
            if self._spa_state == GeckoSpaState.SPA_READY:
                self._facade = GeckoAsyncFacade(self._spa, self)

        finally:
            await self._handle_event(
                GeckoSpaEvent.CONNECTION_FINISHED, facade=self._facade
            )

        # return facade
        return self._facade

    async def async_connect(
        self, spa_identifier: str, spa_address: Optional[str] = None
    ) -> Optional[GeckoAsyncFacade]:
        """Connect to spa.

        This API will connect to the specified spa by doing a search with the
        supplied information. This is probably the API most commonly used by
        automation systems to avoid storing too much information in configuration"""
        _LOGGER.debug("async_connect: ID:%s ADDR:%s", spa_identifier, spa_address)

        spa_descriptors = await self.async_locate_spas(spa_address, spa_identifier)

        assert spa_descriptors is not None
        if len(spa_descriptors) == 0:
            await self._handle_event(
                GeckoSpaEvent.SPA_NOT_FOUND,
                spa_address=spa_address,
                spa_identifier=spa_identifier,
            )
            return None

        return await self.async_connect_to_spa(spa_descriptors[0])

    async def async_set_spa_info(
        self,
        spa_address: Optional[str],
        spa_identifier: Optional[str],
        spa_name: Optional[str],
    ) -> None:
        """Set the spa information so that the sequence pump can run the locate and
        connect phases"""
        _LOGGER.debug(
            "set_spa_info: ADDR:%s ID:%s NAME:%s", spa_address, spa_identifier, spa_name
        )
        self._spa_address = spa_address
        self._spa_identifier = spa_identifier
        self._spa_name = spa_name
        await self.async_reset()

    async def wait_for_descriptors(self) -> None:
        """Wait for descriptors to be available"""
        while self._spa_descriptors is None:
            await asyncio.sleep(0)

    async def wait_for_facade(self) -> None:
        """Wait for facade to be available"""
        while self._facade is None:
            await asyncio.sleep(0)

    ########################################################################
    #
    #   Properties
    #

    @property
    def spa_descriptors(self) -> Optional[List[GeckoAsyncSpaDescriptor]]:
        """Get a list of the discovered spas, or None"""
        return self._spa_descriptors

    @property
    def facade(self) -> Optional[GeckoAsyncFacade]:
        """Get the connected facade, or None"""
        return self._facade

    @property
    def spa_state(self) -> GeckoSpaState:
        """Get the spa state"""
        return self._spa_state

    @property
    def status_line(self) -> str:
        """Get a status line"""
        return self._status_line

    def __str__(self) -> str:
        return f"{self.status_line}"

    ########################################################################
    #
    #   Abstract methods
    #
    @abstractmethod
    async def handle_event(self, event: GeckoSpaEvent, **kwargs) -> None:
        pass

    ########################################################################
    #
    #   Private methods
    #

    async def _handle_event(self, event: GeckoSpaEvent, **kwargs) -> None:
        # Do any pre-processing of the event, such as setting the state or
        # updating the status line
        if event == GeckoSpaEvent.LOCATING_STARTED:
            self._spa_state = GeckoSpaState.LOCATING_SPAS

        elif event == GeckoSpaEvent.LOCATING_FINISHED:
            self._spa_state = GeckoSpaState.IDLE

        elif event == GeckoSpaEvent.SPA_NOT_FOUND:
            self._spa_state = GeckoSpaState.ERROR_SPA_NOT_FOUND

        elif event == GeckoSpaEvent.CONNECTION_STARTED:
            self._spa_state = GeckoSpaState.CONNECTING

        elif event == GeckoSpaEvent.CONNECTION_SPA_COMPLETE:
            self._spa_state = GeckoSpaState.SPA_READY

        elif event == GeckoSpaEvent.CONNECTION_FINISHED:
            if self._facade is not None:
                self._spa_state = GeckoSpaState.CONNECTED

        elif event == GeckoSpaEvent.RUNNING_PING_NO_RESPONSE:
            self._spa_state = GeckoSpaState.ERROR_PING_MISSED
        elif event == GeckoSpaEvent.RUNNING_PING_RECEIVED:
            if self._spa_state in (
                GeckoSpaState.ERROR_PING_MISSED,
                GeckoSpaState.ERROR_RF_FAULT,
                GeckoSpaState.ERROR_NEEDS_ATTENTION,
            ):
                await self.async_reset()

        elif event == GeckoSpaEvent.ERROR_RF_ERROR:
            self._spa_state = GeckoSpaState.ERROR_RF_FAULT

        # elif event == GeckoSpaEvent.RUNNING_SPA_DISCONNECTED:
        #    self._facade = None
        #    self._spa = None
        # self._spa_state = GeckoSpaState.ERROR_NEEDS_ATTENTION

        elif event in (
            GeckoSpaEvent.CONNECTION_PROTOCOL_RETRY_COUNT_EXCEEDED,
            GeckoSpaEvent.ERROR_PROTOCOL_RETRY_COUNT_EXCEEDED,
            GeckoSpaEvent.ERROR_TOO_MANY_RF_ERRORS,
        ):
            self._spa_state = GeckoSpaState.ERROR_NEEDS_ATTENTION

        # TODO: Better please
        self._status_line = f"State: {self._spa_state}, last event {event}"

        # Call the abstract method to allow derived classes to do useful work
        # such as disconnecting handlers when the spa needs to reconnect
        # after protocol failure
        await self.handle_event(event, **kwargs)

        # Any post-processing goes here

    async def _sequence_pump(self) -> None:
        """SpaMan sequence pump coordinates running the manager from the
        parameterized constructor and machine state"""
        _LOGGER.debug("SpaMan sequence pump started")

        try:
            while True:

                if self.spa_state == GeckoSpaState.IDLE:

                    if self._spa_identifier is not None:

                        if self._facade is None:
                            await self.async_connect(
                                self._spa_identifier, self._spa_address
                            )

                    elif self._spa_descriptors is None:
                        await self.async_locate_spas(self._spa_address)

                await asyncio.sleep(0)

        except asyncio.CancelledError:
            _LOGGER.debug("Spaman sequence pump cancelled")
            raise
