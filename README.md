# BBB CAN Sniffer

A CAN bus sniffer for the **BeagleBone Black** (AM335x) that uses the on-chip **PRU co-processors** for nanosecond-accurate hardware timestamping, with a Python/FastAPI backend and a live browser dashboard served over WebSocket.

Beyond raw frame capture, the sniffer includes a full bus-health diagnostics layer: electrical noise detection, protocol error classification, partial/aborted frame detection, and behavioral analysis against a known DBC file.

---

## Features

- **Nanosecond hardware timestamps** — PRU0 captures the SOF falling edge via IEP timer (5 ns resolution), independent of Linux scheduler jitter
- **FIFO timestamp correlation** — PRU timestamps are matched to SocketCAN frames in arrival order; frames without a PRU match still flow through
- **Glitch and noise detection** — PRU classifies dominant pulses shorter than 0.5 bit as `GLITCH` events; bus stuck dominant > 10 bits fires `DOMINANT_RUNAWAY`
- **Partial frame detection** — PRU SOF events with no matching DCAN0 frame within 5 ms are reported as aborted frames
- **Protocol error decoding** — SocketCAN error frames decoded into bit/stuff/CRC/form/ACK error types; TEC and REC polled continuously
- **Behavioral monitoring** — per-message state machine checks periodic timing, babbling transmitters, unexpected IDs, DLC mismatches, and signal range violations (requires DBC file)
- **Three-tier alert system** — CRITICAL / WARN / INFO with per-severity cooldowns and deduplication
- **SQLite logging** — WAL-mode database with 7-day frame retention and 30-day PRU event retention
- **Live browser dashboard** — dark-theme UI over WebSocket, 20 Hz update rate, no laptop software required

---

## Architecture

```
CAN Bus
  │
  ▼
SN65HVD230 transceiver (3.3 V)
  │             │
  │ (DCAN0_RX)  │ (GPIO shadow)
  ▼             ▼
P9.24         P8.45
  │             │
DCAN0         PRU0 (IEP timer)
(kernel)      │
  │           │  DDR ring buffer @ 0x9F000000
  ▼           ▼        (16-byte events, 256 slots)
SocketCAN   pru_shm.py (/dev/mem mmap)
  │           │
  └─────┬─────┘
        ▼
   correlator.py  (FIFO PRU ts ↔ SocketCAN frame)
        │
        ├──► frame_store.py
        ├──► bus_load.py
        └──► diagnostics_aggregator.py
               ├── signal_quality_monitor.py  (glitch/abort)
               ├── error_decoder.py           (SocketCAN error frames)
               ├── tec_rec_poller.py          (ip link, 1 Hz)
               ├── behavioral_monitor.py      (DBC, cycle times, signals)
               └── alert_manager.py           (dedup, cooldowns)
                        │
              ┌─────────┴──────────┐
              ▼                    ▼
         diag_logger.py      FastAPI + WebSocket
         (SQLite WAL)        server.py (20 Hz)
                                   │
                            Browser dashboard
                            (vanilla JS, dark theme)
```

### PRU event types

| Type | Value | Meaning |
|------|-------|---------|
| `SOF` | `0x01` | Valid start-of-frame edge; matched to SocketCAN frame |
| `GLITCH` | `0x02` | Dominant pulse < 1000 ns (0.5 bit at 500 kbit/s); electrical noise |
| `DOMINANT_RUNAWAY` | `0x03` | Bus stuck dominant > 20 µs (10 bits); likely cable fault |

---

## Hardware

### Bill of Materials (~$80)

| Qty | Part | Purpose | ~USD |
|-----|------|---------|------|
| 1 | BeagleBone Black Rev C | AM335x SoC, 2× PRU, 2× DCAN | $55 |
| 1 | SN65HVD230 CAN transceiver module | 3.3 V native — preferred over TJA1050 | $3 |
| 1 | MicroSD 8 GB Class 10 | Debian Bullseye IoT image | $8 |
| 1 | 5 V / 2 A supply | BBB power | $8 |
| 1 | DB9 female connector | CAN bus physical interface | $2 |
| 2 | 120 Ω 1/4 W resistor | Bus termination (one per bus end) | $0.50 |
| 1 | 4.7 kΩ resistor | SN65HVD230 RS pin to GND (high-speed mode) | $0.10 |
| 1 | Small breadboard + jumpers | Assembly | $4 |

> **Why SN65HVD230 over TJA1050?** The SN65HVD230 is natively 3.3 V and connects directly to BBB GPIO without level shifting. The TJA1050 requires a 5 V supply and 3.3 V-tolerant I/O consideration.

### Wiring

| Signal | BBB Header Pin | Ball | SN65HVD230 Pin | Notes |
|--------|---------------|------|----------------|-------|
| DCAN0_TX | P9.26 | A14 | TXD (1) | 3.3 V LVCMOS |
| DCAN0_RX | P9.24 | D15 | RXD (4) | 3.3 V LVCMOS |
| PRU0 RX shadow | P8.45 | R1 | RXD (4) | Y-tap of same RXD wire |
| 3.3 V | P9.3 or P9.4 | — | VCC (3) | |
| GND | P9.1 or P9.2 | — | GND (2) | Common ground |
| RS pin | — | — | RS (8) | Tie to GND for high-speed mode |
| CANH | DB9 pin 7 | — | CANH (7) | To bus high |
| CANL | DB9 pin 2 | — | CANL (6) | To bus low |

P8.45 (ball R1, LCD_DATA0 → `pr1_pru0_pru_r31_0` in mode 6) is Y-wired to P9.24 on the breadboard. Trace length between the two BBB pins is negligible (<5 cm).

### Optional: ADC voltage tap

For DC bus health monitoring (recessive voltage level, termination check), a passive voltage divider can bring CANH into the BBB AIN range:

```
CANH ─── R1 (100 kΩ) ───┬─── AINx (BBB, max 1.8 V)
                         │
                       R2 (82 kΩ)
                         │
                        GND
```

CANH (2.5–3.5 V) maps to 1.13–1.58 V at AINx. At 200 kHz ADC sample rate, individual CAN bits (2 µs at 500 kbit/s) are not resolvable — useful for DC characterization only. Enable via `adc_tap_enabled: true` in config (not yet implemented; see roadmap).

---

## Project Structure

```
BBB_CANsniffer/
├── hardware/
│   └── bom.csv
├── dts/
│   ├── BB-DCAN0-00A0.dts          # Phase 0: DCAN0 pin mux + enable
│   ├── BB-PRU0-CAN-TS-00A0.dts    # Phase 1: PRU GPIO + DDR carveout
│   └── Makefile
├── pru/
│   ├── pru0_timestamp/
│   │   ├── shared_mem.h           # *** Cross-language contract (C ↔ Python) ***
│   │   ├── resource_table.h       # TI remoteproc DDR carveout declaration
│   │   ├── main.c                 # PRU0 IEP timestamp firmware (4-state machine)
│   │   └── Makefile
│   └── pru1_bitbang/              # Phase 4 placeholder
│       ├── main.c
│       └── Makefile
├── backend/
│   ├── pyproject.toml
│   ├── can_sniffer/
│   │   ├── models.py              # Shared dataclasses, enums, alert severity map
│   │   ├── pru_shm.py             # /dev/mem mmap reader + epoch calibration
│   │   ├── socketcan_reader.py    # python-can SocketCAN wrapper
│   │   ├── correlator.py          # FIFO PRU timestamp ↔ CAN frame matcher
│   │   ├── partial_frame_detector.py  # 5 ms SOF timeout → AbortedFrameEvent
│   │   ├── frame_store.py         # Rolling deque of EnrichedFrames
│   │   ├── bus_load.py            # 1-second sliding window utilization
│   │   ├── error_decoder.py       # SocketCAN error frame → ErrorEvent
│   │   ├── tec_rec_poller.py      # `ip -j link show can0` async poller
│   │   ├── signal_quality_monitor.py  # Glitch/abort counting, DOMINANT_RUNAWAY
│   │   ├── behavioral_monitor.py  # DBC-driven per-message state machine
│   │   ├── alert_manager.py       # Dedup, cooldowns, resolution
│   │   ├── diagnostics_aggregator.py  # Fan-out hub → WebSocket snapshot
│   │   ├── diag_logger.py         # SQLite WAL logger (frames + all events)
│   │   └── server.py              # FastAPI + WebSocket, asyncio task orchestration
│   └── tests/
│       ├── test_correlator.py
│       ├── test_bus_load.py
│       ├── test_error_decoder.py
│       ├── test_behavioral_monitor.py
│       └── test_alert_manager.py
├── frontend/
│   ├── index.html                 # Frames, Diagnostics, Stats tabs
│   ├── style.css                  # Dark theme, CSS custom properties
│   └── app.js                     # WebSocket client, ring buffer, diagnostic panels
├── scripts/
│   ├── setup_can.sh               # ip link set can0 up type can bitrate N
│   ├── install_deps.sh            # apt + pip install on BBB
│   └── deploy.sh                  # rsync + service restart
└── systemd/
    ├── pru-loader.service         # Loads PRU firmware via remoteproc
    └── can-sniffer.service        # Starts FastAPI backend
```

> **Critical file:** `pru/pru0_timestamp/shared_mem.h` defines the 16-byte ring buffer event struct shared between the PRU C firmware and Python's `struct.unpack`. Any change to this file must be reflected in both `main.c` and `pru_shm.py` simultaneously.

---

## Setup

### Prerequisites

- BeagleBone Black running **Debian Bullseye** (IoT image, no GUI) from [rcn-ee.net](https://rcn-ee.net/rootfs/bb.org/testing/)
- TI PRU C compiler (`clpru`) from [TI PRU-CGT](https://www.ti.com/tool/PRU-CGT) installed on the BBB or a cross-compile host
- Python 3.11+ (included in Bullseye)
- A DBC file describing the target CAN network (for behavioral monitoring)

---

### Phase 0 — OS Baseline and SocketCAN

**Goal:** `can0` up and receiving real frames from the target bus. No PRU involvement.

#### 1. Flash Debian Bullseye

```bash
# On your workstation
xzcat bone-debian-11.x-iot-armhf-YYYY-MM-DD-4gb.img.xz | dd of=/dev/sdX bs=4M status=progress
```

Boot the BBB from SD, expand the filesystem, set hostname:

```bash
sudo /opt/scripts/tools/grow_partition.sh
hostnamectl set-hostname can-sniffer
```

#### 2. Build and install the DCAN0 device tree overlay

```bash
# On the BBB
cd /opt/can_sniffer/dts
make
sudo make install     # copies .dtbo to /lib/firmware/
```

Edit `/boot/uEnv.txt` to load the overlay:

```
uboot_overlay_addr0=/lib/firmware/BB-DCAN0-00A0.dtbo
```

Reboot. Verify:

```bash
dmesg | grep -i dcan
# Expected: c_can_platform 481cc000.can: c_can_platform device registered
```

#### 3. Bring up the CAN interface

```bash
sudo /opt/can_sniffer/scripts/setup_can.sh 500000
ip -details link show can0
# Expected: <NOARP,UP,LOWER_UP,ECHO> mtu 16 qdisc ... state UP ...
#           can state ERROR-ACTIVE ...
```

#### 4. Verify frame reception

```bash
candump can0
# Frames should appear within milliseconds of bus traffic
```

**Acceptance criteria:** `candump` shows frames; no `BUS-ERROR` or `RX-OVERRUN` after 60 s.

---

### Phase 1 — PRU Firmware

**Goal:** PRU0 captures nanosecond SOF timestamps into the DDR ring buffer.

#### 1. Install PRU CGT

Download `clpru` and `lnkpru` from TI and add to `PATH`, or install the Debian package if available. Also install the PRU software support package:

```bash
sudo apt-get install -y ti-pru-software-support-package
# or clone manually:
git clone https://git.ti.com/cgit/pru-software-support-package/pru-software-support-package.git \
    /usr/lib/ti/pru-software-support-package
```

#### 2. Build and deploy the PRU firmware

```bash
cd /opt/can_sniffer/pru/pru0_timestamp
make
sudo make deploy
# or manually:
sudo cp am335x-pru0-fw /lib/firmware/
```

#### 3. Install the PRU device tree overlay

```bash
cd /opt/can_sniffer/dts
make BB-PRU0-CAN-TS-00A0.dtbo
sudo cp BB-PRU0-CAN-TS-00A0.dtbo /lib/firmware/
```

Add to `/boot/uEnv.txt`:

```
uboot_overlay_addr1=/lib/firmware/BB-PRU0-CAN-TS-00A0.dtbo
```

Reboot. Verify PRU is running:

```bash
dmesg | grep remoteproc
# Expected: remoteproc remoteproc1: remote processor pru0 is now up
cat /sys/class/remoteproc/remoteproc1/state
# Expected: running
```

#### 4. Smoke-test the ring buffer

```python
# Run as root on the BBB
import mmap, struct, time
HDR = struct.Struct("<IIII")
EVT = struct.Struct("<BBHQI")
with open("/dev/mem", "rb") as f:
    mm = mmap.mmap(f.fileno(), 8192, access=mmap.ACCESS_READ, offset=0x9F000000)
magic, widx, _, _ = HDR.unpack_from(mm, 0)
print(f"magic=0x{magic:08X}  write_idx={widx}")
# Expected: magic=0xCAFE1234  write_idx=N (increasing with bus traffic)
```

---

### Phase 2 — Python Backend

**Goal:** Python correlates PRU timestamps with SocketCAN frames and serves data over WebSocket.

#### 1. Install Python dependencies

```bash
sudo /opt/can_sniffer/scripts/install_deps.sh
```

Or manually:

```bash
python3 -m venv /opt/can_sniffer/.venv
/opt/can_sniffer/.venv/bin/pip install -e /opt/can_sniffer/backend
```

#### 2. Place your DBC file

Copy your network DBC file to the BBB and set the path in config (see [Configuration](#configuration)).

#### 3. Start the backend

```bash
sudo /opt/can_sniffer/.venv/bin/uvicorn can_sniffer.server:app \
    --host 0.0.0.0 --port 8000
```

Or install and start the systemd service:

```bash
sudo cp /opt/can_sniffer/systemd/*.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now pru-loader.service
sudo systemctl enable --now can-sniffer.service
```

#### 4. Open the dashboard

On the connected laptop, navigate to `http://<BBB-IP>:8000`. Frames should appear within 200 ms of bus traffic. Default BBB USB-network IP is `192.168.7.2`.

---

### Phase 3 — Browser Dashboard

The frontend is served automatically by the FastAPI backend from `frontend/`. No separate build step required.

**Panels:**

| Tab | Panel | Contents |
|-----|-------|----------|
| Frames | Frame table | PRU timestamp, Arb ID, DLC, data bytes, per-ID delta time (µs) |
| Frames | Bus load bar | Rolling 1-second utilization; colors green → amber → red |
| Diagnostics | Bus health | TEC / REC live, bus state badge, error frames/sec |
| Diagnostics | Protocol errors | Session counts: bit / stuff / CRC / form / ACK errors |
| Diagnostics | Signal quality | Glitches/s, aborted frames/s, dominant runaway status |
| Diagnostics | Missing messages | Per-ID overdue time vs. DBC cycle time |
| Diagnostics | Alert feed | Active CRITICAL / WARN / INFO alerts with age |
| Stats | Summary | Total frames, unique IDs, error events, aborted frames |

**Keyboard / UI controls:**

- **Filter ID** — hex prefix match, applied client-side against a 10 000-frame ring buffer
- **Pause / Resume** — halts table updates without disconnecting WebSocket
- **Clear** — empties the frame ring buffer and DOM table

---

### Phase 4 — PRU1 Bit-Bang CAN (Stretch)

Placeholder firmware in `pru/pru1_bitbang/main.c`. Intended for a second CAN bus channel at ≤ 250 kbit/s using `pr1_pru1_pru_r31_1` (P8.46) and a second SN65HVD230. See roadmap below.

---

## Diagnostics Deep Dive

### Electrical / Signal Quality

| Event | Trigger | Severity |
|-------|---------|---------|
| `GLITCH` | Dominant pulse 1–1000 ns (< 0.5 bit) | SINGLE_GLITCH → INFO; burst ≥ 3/s → WARN |
| `DOMINANT_RUNAWAY` | Bus dominant > 20 µs (10 bits) | CRITICAL |
| Aborted frame | PRU SOF + no SocketCAN frame within 5 ms | ABORTED_FRAME → WARN; ≥ 5/s → CRITICAL |

### Protocol Errors

SocketCAN delivers error frames with the error class bitmask in `arbitration_id`:

| Class bit | Error type |
|-----------|-----------|
| `0x004` | Controller error (TEC/REC in data[6:8]) |
| `0x008` + data[2] bit 0 | Bit error |
| `0x008` + data[2] bit 4 | Bit stuffing error |
| `0x008` + data[2] bit 2 | Frame format error |
| `0x008` + data[3] byte `0x08` | CRC error |
| `0x020` | ACK error (no node acknowledged) |
| `0x040` | Bus-off (TEC ≥ 256) |

TEC and REC are read from `data[6:8]` on controller error frames, and refreshed at 1 Hz via `ip -j -d link show can0`.

### Behavioral Monitoring (requires DBC)

The `BehavioralMonitor` loads a DBC file with `cantools` at startup and maintains a `MessageState` per frame ID. Checks run on every received frame and every 50 ms timeout scan:

| Check | Alert |
|-------|-------|
| ID not in DBC | `UNEXPECTED_ID` INFO |
| DLC ≠ DBC definition | `DLC_MISMATCH` WARN |
| Last RX > 150% cycle time | `MISSING_MSG_TRANSIENT` INFO |
| Last RX > 200% cycle time | `MISSING_MSG` WARN |
| Actual rate > 150% expected | `BABBLING_TX` WARN |
| Signal value outside DBC min/max | `RANGE_VIOLATION` WARN |

### Alert Severity and Cooldowns

| Severity | Cooldown | Dashboard treatment |
|----------|---------|---------------------|
| CRITICAL | 0 s (fires every time condition recurs) | Full-width banner + red badge |
| WARN | 5 s | Alert feed entry, amber |
| INFO | 10 s | Alert feed entry, grey |

Alerts deduplicate by `(category, can_id, signal_name)`. Resolution fires a `BUS_RECOVERY` INFO alert and marks the original resolved in SQLite.

### SQLite Log Schema

Database is written at `can_sniffer.db` in the working directory. Tables:

| Table | Retention | Contents |
|-------|-----------|---------|
| `can_frames` | 7 days | Every received frame (ts, can_id, dlc, data, pru_ts_ns, is_aborted) |
| `error_events` | 7 days | SocketCAN error frames + TEC/REC |
| `pru_events` | 30 days | All PRU ring buffer events (SOF, GLITCH, RUNAWAY) |
| `behavioral_alerts` | 90 days | All fired alerts with severity and resolution state |
| `bus_state_log` | 30 days | TEC/REC/state snapshots at 1 Hz |

WAL mode with `synchronous=NORMAL` — safe on crash, readable concurrently by external tools.

Query examples:

```sql
-- Error events in the last hour
SELECT datetime(ts,'unixepoch'), tec, rec FROM error_events
WHERE ts > strftime('%s','now') - 3600 ORDER BY ts DESC;

-- Glitch rate by minute
SELECT strftime('%Y-%m-%d %H:%M', ts, 'unixepoch') AS minute,
       COUNT(*) AS glitches
FROM pru_events WHERE event_type = 2
GROUP BY minute ORDER BY minute DESC LIMIT 30;

-- All CRITICAL alerts this session
SELECT datetime(ts,'unixepoch'), category, detail FROM behavioral_alerts
WHERE severity = 'CRITICAL' ORDER BY ts DESC;
```

---

## Configuration

Environment variables (set in the systemd service or shell):

| Variable | Default | Description |
|----------|---------|-------------|
| `CAN_INTERFACE` | `can0` | SocketCAN interface name |
| `CAN_BITRATE` | `500000` | Bus bitrate in bit/s |
| `DBC_PATH` | _(none)_ | Absolute path to DBC file; behavioral monitoring disabled if unset |
| `DB_PATH` | `can_sniffer.db` | SQLite database path |
| `PRU_SHM_ADDR` | `0x9F000000` | Physical address of DDR carveout (must match DTS) |
| `SERVER_HOST` | `0.0.0.0` | FastAPI bind address |
| `SERVER_PORT` | `8000` | FastAPI bind port |

---

## Development

### Running tests on the host (no BBB required)

The Python unit tests have no hardware dependencies — they use synthetic timestamps and in-memory state. `pru_shm.py` and `socketcan_reader.py` are not tested at the unit level (they require real hardware).

```bash
cd backend
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/pytest -v
```

### Deploying changes to the BBB

```bash
BBB_HOST=192.168.7.2 BBB_USER=debian ./scripts/deploy.sh
```

This rsyncs the repo (excluding `.git`, `__pycache__`, `.venv`, `*.db`) and restarts `can-sniffer.service`.

### Rebuilding PRU firmware after changes

```bash
cd pru/pru0_timestamp
make clean && make
BBB_HOST=192.168.7.2 BBB_USER=debian make deploy
```

`make deploy` copies the firmware to `/lib/firmware/am335x-pru0-fw` and cycles `remoteproc1` via sysfs.

### Changing CAN bitrate

The bit-timing thresholds in `shared_mem.h` are compiled into the PRU firmware:

```c
#define GLITCH_THRESHOLD_COUNTS  200U   /* 1000 ns = 0.5 bit at 500 kbit/s */
#define SOF_MAX_COUNTS          4000U   /* 20000 ns = 10 bits               */
#define IFS_COUNTS              1200U   /* 6000 ns = 3 bits (IFS)           */
```

If you change the bus bitrate, recalculate these constants (IEP ticks = time_ns / 5) and rebuild the PRU firmware before restarting. The Python backend reads the bitrate from `CAN_BITRATE` at startup and does not need recompilation.

### `shared_mem.h` is the cross-language contract

The 16-byte ring buffer event struct is defined once in C and read by Python using `struct.unpack("<BBHQI")`. Both sides must agree:

```
Offset  Size  Field
     0     1  type       (PruEventType enum: 0x01 SOF, 0x02 GLITCH, 0x03 DOMINANT_RUNAWAY)
     1     1  flags      (bit 0: IEP rollover since previous entry)
     2     2  seq        (monotonic uint16, wraps at 65535)
     4     8  t_fall_ns  (uint64 monotonic ns since PRU start; Python adds epoch_offset)
    12     4  pulse_ns   (dominant pulse width in ns; 0 for SOF)
```

Any change to this layout requires updating both `pru/pru0_timestamp/main.c` and `backend/can_sniffer/pru_shm.py` in the same commit.

---

## Bus Impact — Is the Sniffer Invisible?

**Short answer:** With `listen-only on` (the default in `setup_can.sh`), the sniffer is electrically passive and invisible to all other bus nodes. Without it, the DCAN0 controller participates in the protocol.

### Three interaction mechanisms

| Mechanism | Default mode | Listen-only mode |
|-----------|-------------|-----------------|
| **ACK bit** | DCAN0 acknowledges every correctly received frame | No ACK sent — controller is fully passive |
| **Error frames** | DCAN0 transmits a 6-bit error flag if it detects a bus error, aborting the in-progress frame | No error frames sent |
| **Physical load** | SN65HVD230 adds ~10–20 pF capacitance | Same — unavoidable but negligible on typical bus |

#### Why ACK matters

The ACK slot is a wired-OR: if *any* receiver drives it dominant, the transmitter sees an ACK. In default mode, our sniffer will ACK every frame it receives. The consequence: if a real receiver goes offline, our sniffer masks the resulting ACK error — the transmitter will never know the intended recipient is gone. In listen-only mode this cannot happen.

#### What listen-only mode costs

- **TEC stays zero.** The controller cannot go bus-off (it never transmits, so the transmit error counter never increments). This is fine — and desirable.
- **Some diagnostic sensitivity is reduced.** In listen-only mode, the controller detects errors internally and reports them via SocketCAN error frames to userspace, but only for errors it can observe as a receiver (stuff, CRC, form, bit-level issues visible on RX). ACK errors on frames we transmit are not applicable. `berr-reporting on` still tracks REC.

### Termination — the critical hardware note

**Do not add a 120 Ω termination resistor unless your sniffer is physically at one of the two bus endpoints.**

A properly wired CAN bus has exactly two 120 Ω termination resistors — one at each physical end. Adding a third in the middle creates a parallel impedance of 40 Ω, causing signal reflections and degraded waveforms. If you are mid-bus tapping (the common case), wire CANH/CANL directly to the bus and leave the termination resistors alone. Only populate the 120 Ω resistor in `hardware/bom.csv` if you are replacing a bus-end terminator.

## Known Limitations

- **PRU0 IEP timer rollover:** The AM335x IEP is 32-bit at 200 MHz — it rolls over every ~21.5 s. The PRU firmware tracks rollovers in a local counter; `t_fall_ns` in each ring buffer event is a monotonically increasing uint64. Python adds a one-time epoch offset calibrated at startup. Accuracy degrades by ±50 ppm (AM335x crystal tolerance) over time; restart the backend to recalibrate.
- **SocketCAN error frames do not report CRC errors as a dedicated class:** CRC errors are inferred from `data[3]` (protocol violation location byte) when `CAN_ERR_PROT` is set. This is a Linux kernel limitation, not a firmware one.
- **Aborted frame detection has a 5 ms latency:** By design — the timeout must exceed worst-case Linux socket delivery latency. Isolated aborted frames appear in the dashboard within 5–6 ms of the bus event.
- **Web UI has no authentication:** The FastAPI server binds to `0.0.0.0:8000` with no access control. Use an SSH tunnel or restrict binding to `127.0.0.1` on shared networks.
- **Single CAN channel:** Only DCAN0 (`can0`) is currently implemented. DCAN1 and PRU1 bit-bang are planned (see roadmap).
- **DBC signals with no min/max defined:** `cantools` returns `None` for `signal.minimum` / `signal.maximum`; range checking is silently skipped for those signals.

---

## Roadmap

- [ ] **Config file** — YAML/TOML config for bitrate, DBC path, DB path, ADC tap enable
- [ ] **Historical query API** — REST endpoints for querying `can_sniffer.db` (`/api/frames`, `/api/alerts`, `/api/errors`) for post-hoc analysis
- [ ] **ADC tap** — optional `adc_reader.py` reading `/sys/bus/iio/devices/iio:device0/` for bus DC health metrics
- [ ] **DCAN1 second channel** — second SocketCAN interface (`can1`) via DCAN1, multiplexed in the WebSocket JSON as `"channel": 1`
- [ ] **PRU1 bit-bang CAN** — software CAN receiver for a third channel at ≤ 250 kbit/s (`pru/pru1_bitbang/`)
- [ ] **Frame export** — download captured frames as CSV or `.blf` (BLF logging format)
- [ ] **SSH auth for dashboard** — HTTP Basic Auth or mTLS option for the FastAPI server
- [ ] **Eye diagram approximation** — PRU multi-point sub-bit sampling to estimate bit edge quality

---

## License

MIT. See `LICENSE`.

---

## References

- [AM335x Technical Reference Manual](https://www.ti.com/lit/ug/spruh73q/spruh73q.pdf) — PRU architecture, IEP timer (Chapter 4), DCAN (Chapter 16)
- [BeagleBone Black System Reference Manual](https://github.com/beagleboard/beaglebone-black/wiki/System-Reference-Manual)
- [TI PRU Software Support Package](https://git.ti.com/cgit/pru-software-support-package/pru-software-support-package.git)
- [SocketCAN documentation](https://www.kernel.org/doc/html/latest/networking/can.html) — error frame format
- [cantools](https://cantools.readthedocs.io/) — Python DBC/KCD/SYM parser and decoder
- [python-can](https://python-can.readthedocs.io/) — SocketCAN Python interface
