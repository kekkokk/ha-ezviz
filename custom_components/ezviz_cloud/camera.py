"""Support ezviz camera devices."""
from __future__ import annotations

import logging

from pyezviz.exceptions import HTTPError, InvalidHost, PyEzvizError
import voluptuous as vol

from homeassistant.components import ffmpeg
from homeassistant.components.camera import PLATFORM_SCHEMA, SUPPORT_STREAM, Camera
from homeassistant.components.ffmpeg import DATA_FFMPEG
from homeassistant.config_entries import (
    SOURCE_DISCOVERY,
    SOURCE_IGNORE,
    SOURCE_IMPORT,
    ConfigEntry,
)
from homeassistant.const import CONF_IP_ADDRESS, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv, entity_platform
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType

from .const import (
    ATTR_DIRECTION,
    ATTR_ENABLE,
    ATTR_LEVEL,
    ATTR_SERIAL,
    ATTR_SPEED,
    ATTR_TYPE,
    CONF_CAMERAS,
    CONF_FFMPEG_ARGUMENTS,
    DATA_COORDINATOR,
    DEFAULT_CAMERA_USERNAME,
    DEFAULT_FFMPEG_ARGUMENTS,
    DEFAULT_RTSP_PORT,
    DIR_DOWN,
    DIR_LEFT,
    DIR_RIGHT,
    DIR_UP,
    DOMAIN,
    SERVICE_ALARM_SOUND,
    SERVICE_ALARM_TRIGER,
    SERVICE_DETECTION_SENSITIVITY,
    SERVICE_PTZ,
    SERVICE_WAKE_DEVICE,
)
from .coordinator import EzvizDataUpdateCoordinator
from .entity import EzvizEntity

CAMERA_SCHEMA = vol.Schema(
    {vol.Required(CONF_USERNAME): cv.string, vol.Required(CONF_PASSWORD): cv.string}
)

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend(
    {
        vol.Required(CONF_USERNAME): cv.string,
        vol.Required(CONF_PASSWORD): cv.string,
        vol.Optional(CONF_CAMERAS, default={}): {cv.string: CAMERA_SCHEMA},
    }
)

_LOGGER = logging.getLogger(__name__)


async def async_setup_platform(
    hass: HomeAssistant,
    config: ConfigType,
    async_add_entities: entity_platform.AddEntitiesCallback,
    discovery_info: DiscoveryInfoType | None = None,
) -> None:
    """Set up a Ezviz IP Camera from platform config."""
    _LOGGER.warning(
        "Loading ezviz via platform config is deprecated, it will be automatically imported. Please remove it afterwards"
    )

    # Check if entry config exists and skips import if it does.
    if hass.config_entries.async_entries(DOMAIN):
        return

    # Check if importing camera account.
    if CONF_CAMERAS in config:
        cameras_conf = config[CONF_CAMERAS]
        for serial, camera in cameras_conf.items():
            hass.async_create_task(
                hass.config_entries.flow.async_init(
                    DOMAIN,
                    context={"source": SOURCE_IMPORT},
                    data={
                        ATTR_SERIAL: serial,
                        CONF_USERNAME: camera[CONF_USERNAME],
                        CONF_PASSWORD: camera[CONF_PASSWORD],
                    },
                )
            )

    # Check if importing main ezviz cloud account.
    hass.async_create_task(
        hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": SOURCE_IMPORT},
            data=config,
        )
    )


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: entity_platform.AddEntitiesCallback,
) -> None:
    """Set up Ezviz cameras based on a config entry."""

    coordinator: EzvizDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id][
        DATA_COORDINATOR
    ]

    camera_entities = []

    for camera, value in coordinator.data.items():

        camera_rtsp_entry = [
            item
            for item in hass.config_entries.async_entries(DOMAIN)
            if item.unique_id == camera and item.source != SOURCE_IGNORE
        ]

        # There seem to be a bug related to localRtspPort in Ezviz API.
        local_rtsp_port = (
            value["local_rtsp_port"]
            if value["local_rtsp_port"] != 0
            else DEFAULT_RTSP_PORT
        )

        if camera_rtsp_entry:

            ffmpeg_arguments = camera_rtsp_entry[0].options[CONF_FFMPEG_ARGUMENTS]
            camera_username = camera_rtsp_entry[0].data[CONF_USERNAME]
            camera_password = camera_rtsp_entry[0].data[CONF_PASSWORD]

            camera_rtsp_stream = f"rtsp://{camera_username}:{camera_password}@{value['local_ip']}:{local_rtsp_port}{ffmpeg_arguments}"
            _LOGGER.debug("Camera %s source stream: %s", camera, camera_rtsp_stream)

        else:

            hass.async_create_task(
                hass.config_entries.flow.async_init(
                    DOMAIN,
                    context={"source": SOURCE_DISCOVERY},
                    data={
                        ATTR_SERIAL: camera,
                        CONF_IP_ADDRESS: value["local_ip"],
                    },
                )
            )

            _LOGGER.warning(
                "Found camera with serial %s without configuration. Please go to integration to complete setup",
                camera,
            )

            ffmpeg_arguments = DEFAULT_FFMPEG_ARGUMENTS
            camera_username = DEFAULT_CAMERA_USERNAME
            camera_password = None
            camera_rtsp_stream = ""

        camera_entities.append(
            EzvizCamera(
                hass,
                coordinator,
                camera,
                camera_username,
                camera_password,
                camera_rtsp_stream,
                local_rtsp_port,
                ffmpeg_arguments,
            )
        )

    async_add_entities(camera_entities)

    platform = entity_platform.async_get_current_platform()

    platform.async_register_entity_service(
        SERVICE_PTZ,
        {
            vol.Required(ATTR_DIRECTION): vol.In(
                [DIR_UP, DIR_DOWN, DIR_LEFT, DIR_RIGHT]
            ),
            vol.Required(ATTR_SPEED): cv.positive_int,
        },
        "perform_ptz",
    )

    platform.async_register_entity_service(
        SERVICE_ALARM_TRIGER,
        {
            vol.Required(ATTR_ENABLE): cv.positive_int,
        },
        "perform_sound_alarm",
    )

    platform.async_register_entity_service(
        SERVICE_WAKE_DEVICE, {}, "perform_wake_device"
    )

    platform.async_register_entity_service(
        SERVICE_ALARM_SOUND,
        {vol.Required(ATTR_LEVEL): cv.positive_int},
        "perform_alarm_sound",
    )

    platform.async_register_entity_service(
        SERVICE_DETECTION_SENSITIVITY,
        {
            vol.Required(ATTR_LEVEL): cv.positive_int,
            vol.Required(ATTR_TYPE): cv.positive_int,
        },
        "perform_set_alarm_detection_sensibility",
    )


class EzvizCamera(EzvizEntity, Camera):
    """An implementation of a Ezviz security camera."""

    coordinator: EzvizDataUpdateCoordinator

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: EzvizDataUpdateCoordinator,
        serial: str,
        camera_username: str,
        camera_password: str | None,
        camera_rtsp_stream: str | None,
        local_rtsp_port: int,
        ffmpeg_arguments: str | None,
    ) -> None:
        """Initialize a Ezviz security camera."""
        super().__init__(coordinator, serial)
        Camera.__init__(self)
        self._username = camera_username
        self._password = camera_password
        self._rtsp_stream = camera_rtsp_stream
        self._local_rtsp_port = local_rtsp_port
        self._ffmpeg_arguments = ffmpeg_arguments
        self._ffmpeg = hass.data[DATA_FFMPEG]
        self._attr_unique_id = serial
        self._attr_name = self.data["name"]

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        return self.data["status"] != 2

    @property
    def supported_features(self) -> int:
        """Return supported features."""
        if self._password:
            return SUPPORT_STREAM
        return 0

    @property
    def is_on(self) -> bool:
        """Return true if on."""
        return bool(self.data["status"])

    @property
    def is_recording(self) -> bool:
        """Return true if the device is recording."""
        return self.data["alarm_notify"]

    @property
    def motion_detection_enabled(self) -> bool:
        """Camera Motion Detection Status."""
        return self.data["alarm_notify"]

    def enable_motion_detection(self) -> None:
        """Enable motion detection in camera."""
        try:
            self.coordinator.ezviz_client.set_camera_defence(self._serial, 1)

        except InvalidHost as err:
            raise InvalidHost("Error enabling motion detection") from err

    def disable_motion_detection(self) -> None:
        """Disable motion detection."""
        try:
            self.coordinator.ezviz_client.set_camera_defence(self._serial, 0)

        except InvalidHost as err:
            raise InvalidHost("Error disabling motion detection") from err

    async def async_camera_image(
        self, width: int | None = None, height: int | None = None
    ) -> bytes | None:
        """Return a frame from the camera stream."""
        if self._rtsp_stream is None:
            return None
        return await ffmpeg.async_get_image(
            self.hass, self._rtsp_stream, width=width, height=height
        )

    async def stream_source(self) -> str | None:
        """Return the stream source."""
        if self._password is None:
            return None
        local_ip = self.data["local_ip"]
        self._rtsp_stream = (
            f"rtsp://{self._username}:{self._password}@"
            f"{local_ip}:{self._local_rtsp_port}{self._ffmpeg_arguments}"
        )
        _LOGGER.debug("Camera %s source stream: %s", self._serial, self._rtsp_stream)

        return self._rtsp_stream

    def perform_ptz(self, direction: str, speed: int) -> None:
        """Perform a PTZ action on the camera."""
        try:
            self.coordinator.ezviz_client.ptz_control(
                str(direction).upper(), self._serial, "START", speed
            )
            self.coordinator.ezviz_client.ptz_control(
                str(direction).upper(), self._serial, "STOP", speed
            )

        except HTTPError as err:
            raise HTTPError("Cannot perform PTZ") from err

    def perform_sound_alarm(self, enable: int) -> None:
        """Sound the alarm on a camera."""
        try:
            self.coordinator.ezviz_client.sound_alarm(self._serial, enable)
        except HTTPError as err:
            raise HTTPError("Cannot sound alarm") from err

    def perform_wake_device(self) -> None:
        """Basically wakes the camera by querying the device."""
        try:
            self.coordinator.ezviz_client.get_detection_sensibility(self._serial)
        except (HTTPError, PyEzvizError) as err:
            raise PyEzvizError("Cannot wake device") from err

    def perform_alarm_sound(self, level: int) -> None:
        """Enable/Disable movement sound alarm."""
        try:
            self.coordinator.ezviz_client.alarm_sound(self._serial, level, 1)
        except HTTPError as err:
            raise HTTPError(
                "Cannot set alarm sound level for on movement detected"
            ) from err

    def perform_set_alarm_detection_sensibility(
        self, level: int, type_value: int
    ) -> None:
        """Set camera detection sensibility level service."""
        try:
            self.coordinator.ezviz_client.detection_sensibility(
                self._serial, level, type_value
            )
        except (HTTPError, PyEzvizError) as err:
            raise PyEzvizError("Cannot set detection sensitivity level") from err
