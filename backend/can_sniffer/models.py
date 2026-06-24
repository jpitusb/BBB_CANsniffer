from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


@dataclass
class EnrichedFrame:
    arb_id:      int
    dlc:         int
    data:        bytes
    is_extended: bool
    kernel_ts:   float          # time.time() epoch from python-can
    channel:     int = 0

    def to_dict(self) -> dict:
        return {
            "arb_id":      f"0x{self.arb_id:08X}" if self.is_extended else f"0x{self.arb_id:03X}",
            "dlc":         self.dlc,
            "data":        " ".join("%02X" % b for b in self.data),
            "is_extended": self.is_extended,
            "kernel_ts":   self.kernel_ts,
            "channel":     self.channel,
        }


@dataclass
class ErrorEvent:
    ts:         float
    error_class: int
    tec:        Optional[int]
    rec:        Optional[int]
    bit_error:  bool
    stuff_error: bool
    form_error: bool
    crc_error:  bool
    ack_error:  bool
    bus_off:    bool
    restarted:  bool
    raw_data:   bytes


class BusState(str, Enum):
    ACTIVE        = "error-active"
    ERROR_WARNING = "error-warning"
    ERROR_PASSIVE = "error-passive"
    BUS_OFF       = "bus-off"
    UNKNOWN       = "unknown"


class AlertSeverity(str, Enum):
    INFO     = "INFO"
    WARN     = "WARN"
    CRITICAL = "CRITICAL"


class AlertCategory(str, Enum):
    BUS_OFF                = "BUS_OFF"
    ERROR_PASSIVE          = "ERROR_PASSIVE"
    ERROR_WARNING          = "ERROR_WARNING"
    MISSING_MSG            = "MISSING_MSG"
    MISSING_MSG_TRANSIENT  = "MISSING_MSG_TRANSIENT"
    BABBLING_TX            = "BABBLING_TX"
    UNEXPECTED_ID          = "UNEXPECTED_ID"
    DLC_MISMATCH           = "DLC_MISMATCH"
    RANGE_VIOLATION        = "RANGE_VIOLATION"
    BUS_RECOVERY           = "BUS_RECOVERY"
    CONTROLLER_RESTARTED   = "CONTROLLER_RESTARTED"
    REPEATED_ERROR_FRAMES  = "REPEATED_ERROR_FRAMES"


ALERT_SEVERITY_MAP: dict[AlertCategory, AlertSeverity] = {
    AlertCategory.BUS_OFF:               AlertSeverity.CRITICAL,
    AlertCategory.ERROR_PASSIVE:         AlertSeverity.CRITICAL,
    AlertCategory.ERROR_WARNING:         AlertSeverity.WARN,
    AlertCategory.REPEATED_ERROR_FRAMES: AlertSeverity.WARN,
    AlertCategory.MISSING_MSG:           AlertSeverity.WARN,
    AlertCategory.BABBLING_TX:           AlertSeverity.WARN,
    AlertCategory.RANGE_VIOLATION:       AlertSeverity.WARN,
    AlertCategory.DLC_MISMATCH:          AlertSeverity.WARN,
    AlertCategory.UNEXPECTED_ID:         AlertSeverity.INFO,
    AlertCategory.MISSING_MSG_TRANSIENT: AlertSeverity.INFO,
    AlertCategory.BUS_RECOVERY:          AlertSeverity.INFO,
    AlertCategory.CONTROLLER_RESTARTED:  AlertSeverity.INFO,
}

ALERT_COOLDOWN_S: dict[AlertSeverity, float] = {
    AlertSeverity.CRITICAL: 0.0,
    AlertSeverity.WARN:     5.0,
    AlertSeverity.INFO:     10.0,
}


@dataclass
class Alert:
    alert_id:    str
    severity:    AlertSeverity
    category:    AlertCategory
    msg:         str
    ts:          float
    can_id:      Optional[int] = None
    signal_name: Optional[str] = None
    count:       int = 1
    resolved:    bool = False
    resolved_ts: Optional[float] = None

    @staticmethod
    def make_id(category: AlertCategory, can_id: Optional[int],
                signal_name: Optional[str]) -> str:
        key = f"{category.value}:{can_id}:{signal_name}"
        return hashlib.md5(key.encode()).hexdigest()[:6]

    def to_dict(self) -> dict:
        return {
            "alert_id":    self.alert_id,
            "severity":    self.severity.value,
            "category":    self.category.value,
            "msg":         self.msg,
            "ts":          self.ts,
            "can_id":      f"0x{self.can_id:X}" if self.can_id is not None else None,
            "signal_name": self.signal_name,
            "count":       self.count,
            "resolved":    self.resolved,
        }
