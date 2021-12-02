"""Provides the ezviz DataUpdateCoordinator."""
from datetime import timedelta
import logging

from async_timeout import timeout
from pyezviz.client import EzvizClient
from pyezviz.exceptions import (
    EzvizAuthTokenExpired,
    EzvizAuthVerificationCode,
    HTTPError,
    InvalidURL,
    PyEzvizError,
)

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import (
    ConfigEntryAuthFailed,
    DataUpdateCoordinator,
    UpdateFailed,
)

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


class EzvizDataUpdateCoordinator(DataUpdateCoordinator):
    """Class to manage fetching Ezviz data."""

    def __init__(
        self, hass: HomeAssistant, *, api: EzvizClient, api_timeout: int
    ) -> None:
        """Initialize global Ezviz data updater."""
        self.ezviz_client = api
        self._api_timeout = api_timeout
        update_interval = timedelta(seconds=30)

        super().__init__(hass, _LOGGER, name=DOMAIN, update_interval=update_interval)

    async def _async_update_data(self) -> dict:
        """Fetch data from Ezviz."""
        try:
            async with timeout(self._api_timeout):
                return await self.hass.async_add_executor_job(
                    self.ezviz_client.load_cameras
                )

        except (EzvizAuthTokenExpired, EzvizAuthVerificationCode) as error:
            raise ConfigEntryAuthFailed from error

        except (InvalidURL, HTTPError, PyEzvizError) as error:
            raise UpdateFailed(f"Invalid response from API: {error}") from error
