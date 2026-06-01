from __future__ import annotations

import time
from typing import Optional

import can

from .models import ErrorEvent

# SocketCAN error class bits carried in arbitration_id (CAN_ERR_FLAG already stripped)
_ERR_TX_TIMEOUT   = 0x001
_ERR_LOSTARB      = 0x002
_ERR_CRTL         = 0x004
_ERR_PROT         = 0x008
_ERR_TRX          = 0x010
_ERR_ACK          = 0x020
_ERR_BUSOFF       = 0x040
_ERR_BUSERROR     = 0x080
_ERR_RESTARTED    = 0x100

# data[2]: protocol violation type bits (when _ERR_PROT is set)
_PROT_BIT         = 0x01   # single bit error
_PROT_FORM        = 0x02   # frame format error
_PROT_STUFF       = 0x04   # bit stuffing error

# data[3]: protocol violation location (when _ERR_PROT is set)
_LOC_CRC_SEQ      = 0x08   # CRC sequence


class ErrorDecoder:
    def decode(self, msg: can.Message) -> Optional[ErrorEvent]:
        if not msg.is_error_frame:
            return None

        ec   = msg.arbitration_id
        data = bytes(msg.data) if msg.data else b"\x00" * 8
        data = data.ljust(8, b"\x00")

        prot_type = data[2] if ec & _ERR_PROT else 0
        prot_loc  = data[3] if ec & _ERR_PROT else 0

        # TEC and REC are embedded in data[6:8] when a controller error is reported
        tec: Optional[int] = data[6] if ec & _ERR_CRTL else None
        rec: Optional[int] = data[7] if ec & _ERR_CRTL else None

        return ErrorEvent(
            ts          = msg.timestamp or time.time(),
            error_class = ec,
            tec         = tec,
            rec         = rec,
            bit_error   = bool(prot_type & _PROT_BIT),
            stuff_error = bool(prot_type & _PROT_STUFF),
            form_error  = bool(prot_type & _PROT_FORM),
            crc_error   = bool(ec & _ERR_PROT and prot_loc & _LOC_CRC_SEQ),
            ack_error   = bool(ec & _ERR_ACK),
            bus_off     = bool(ec & _ERR_BUSOFF),
            restarted   = bool(ec & _ERR_RESTARTED),
            raw_data    = data,
        )
