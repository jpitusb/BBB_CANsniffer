# BBB CAN Sniffer

A CAN bus sniffer for the **BeagleBone Black** (AM335x) that uses the on-chip **PRU co-processors** for nanosecond-accurate hardware timestamping, with a Python/FastAPI backend and a live browser dashboard served over WebSocket.

Beyond raw frame capture, the sniffer includes a full bus-health diagnostics layer: electrical noise detection, protocol error classification, partial/aborted frame detection, and behavioral analysis against a known DBC file.

---

## Features

- **Nanosecond hardware timestamps** — PRU0 captures the SOF falling edge via IEP timer (1 ns resolution), independent of Linux scheduler jitter
- **FIFO timestamp correlation** — PRU timestamps are matched to SocketCAN frames in arrival order; frames without a PRU match still flow through
- **Glitch and noise detection** — PRU classifies dominant pulses shorter than 0.5 bit as `GLITCH` events; bus stuck dominant > 10 bits fires `DOMINANT_RUNAWAY`
- **Partial frame detection** — PRU SOF events with no matching CAN frame within 5 ms are reported as aborted frames
- **Protocol error decoding** — SocketCAN error frames decoded into bit/stuff/CRC/form/ACK error types; TEC and REC polled continuously
- **Behavioral monitoring** — per-message state machine checks periodic timing, babbling transmitters, unexpected IDs, DLC mismatches, and signal range violations (requires DBC file)
- **Three-tier alert system** — CRITICAL / WARN / INFO with per-severity cooldowns and deduplication
- **SQLite logging** — WAL-mode database with 7-day frame retention and 30-day PRU event retention
- **Live browser dashboard** — dark-theme UI over WebSocket, 5 Hz update rate, no laptop software required
- **Diagnostic graphs** — uPlot charts (bus load, TEC/REC, error rate, latency trend, activity heatmap, per-ID interval and PRU-vs-kernel histograms) served locally, no CDN
- **BBB health monitoring** — live CPU, memory, temperature, load average, uptime, and disk usage
- **Per-ID timing statistics** — rolling min/max/mean/σ/p95/p99 inter-frame intervals and jitter RMS; rows highlight when jitter exceeds 10% of mean
- **Request/response latency** — configurable address pairs (explicit or pattern-based); measures µs latency between matched request and response frames; edit live from the dashboard
- **Pre/post-trigger capture** — arm on arb_id / error frame / bus load threshold / manual fire; snapshots 200 frames before and after trigger; download as JSON or SVG sequence diagram
- **SVG sequence diagrams** — swimlane diagram per captured session; latency pair arrows with measured times; trigger-point marker
- **Frame annotation** — double-click any frame row to attach a text note; stored in SQLite

---

## Installation

### BBB #1 — Sniffer (quick start)

> **Required kernel:** Debian **Buster** (4.19-ti).  The 5.10-ti kernel (Bookworm) sets
> `PRUSS_CFG.GPCFG0` bit 25 (MII_RT mode), permanently locking `R31[15:0]` to MII_RT
> signals and preventing direct GPI pin reads.  4.19-ti has the same issue; the firmware
> works around it via the PRUSS INTC path (GPIO2_7 → system event 24 → R31[30]).
>
> Image: `bone-debian-10.x-iot-armhf-YYYY-MM-DD-4gb.img.xz` from
> `https://rcn-ee.net/rootfs/bb.org/testing/`

```bash
# 1. Flash Debian Buster IoT image, boot, SSH in as debian/temppwd

# 2. Fix apt sources (Buster is EOL; standard repos moved to archive)
sudo bash -c "cat > /etc/apt/sources.list <<'EOF'
deb http://archive.debian.org/debian buster main contrib non-free
deb http://archive.debian.org/debian-security buster/updates main
deb https://repos.rcn-ee.com/debian buster main
EOF"
sudo apt-key adv --keyserver keyserver.ubuntu.com --recv-keys D284E608A4C46402

# 3. First-time bootstrap — clones repo, patches /boot/uEnv.txt, reboots
curl -sL https://raw.githubusercontent.com/jpitusb/BBB_CANsniffer/master/scripts/bootstrap.sh \
    | sudo bash
# --- board reboots automatically ---

# 4. After reboot — build and install everything
sudo /opt/can_sniffer/scripts/install_deps.sh

# Done. Dashboard is at http://<BBB-IP>:8000/
```

Services (`pru-loader` + `can-sniffer`) start automatically on every subsequent boot.

> **What bootstrap.sh does:** sets the hostname (`can-sniffer`), clones the repo to
> `/opt/can_sniffer`, patches four required lines into `/boot/uEnv.txt` (PRU overlay,
> disable video cape, disable cape-universal, DDR memmap), and reboots.

> **What install_deps.sh does:** auto-detects Python version (3.7 on Buster), installs
> apt packages (`gcc-pru`, `can-utils`, etc.), installs Python dependencies with
> appropriate version constraints, builds the PRU firmware and DTS overlay, installs and
> enables the systemd services.

### BBB #2 — Traffic / fault generator (quick start)

Wire the second BBB (see [Hardware → BBB #2 wiring](#bbb-2--traffic--fault-generator-wiring)), then:

```bash
# Same bootstrap step as BBB #1
curl -sL https://raw.githubusercontent.com/jpitusb/BBB_CANsniffer/master/scripts/bootstrap.sh \
    | sudo bash

# After reboot, run the generator setup instead of install_deps.sh
sudo /opt/can_sniffer/tools/can_gen/setup_bbb2.sh

# Generate traffic
sudo python3 /opt/can_sniffer/tools/can_gen/generator.py --list
sudo python3 /opt/can_sniffer/tools/can_gen/generator.py -s normal --loop

# Command/response load (1 master polling 6 nodes ~10x/sec each, ~120 fps).
# Mirrors a real command/response bus and populates the Latency tab/graph.
sudo python3 /opt/can_sniffer/tools/can_gen/generator.py -s cmd_resp --loop
```

### After every reboot — two-board test setup

BBB #1 boots into **listen-only** mode (passive sniffer, no ACKs). For BBB #2's frames
to transmit cleanly, BBB #1 must ACK them. Switch it to normal mode before running the
generator, then back to listen-only when done.

**BBB #1:**
```bash
# Switch to normal mode so it ACKs BBB #2's frames
# (stops the service, reconfigures can1, restarts the service)
sudo systemctl stop can-sniffer
sudo bash /opt/can_sniffer/scripts/setup_can_mode.sh normal
sudo systemctl start can-sniffer
```

**BBB #2:**
```bash
# Bring up can1 and start generating
sudo bash /opt/can_sniffer/scripts/setup_can_mode.sh normal
sudo python3 /opt/can_sniffer/tools/can_gen/generator.py -s normal --loop
```

When finished, restore BBB #1 to passive sniffer mode:
```bash
# On BBB #1 — setup_can_mode.sh listen restarts the service automatically
sudo systemctl stop can-sniffer
sudo bash /opt/can_sniffer/scripts/setup_can_mode.sh listen
```

> **Why stop the service first?** `can-sniffer.service` holds the `can1` socket open.
> `ip link set can1 down` fails while the socket is held, so the mode switch is blocked
> unless the service is stopped first.

> **BBB #2 bus-off recovery:** If BBB #1 was in listen-only mode while BBB #2 was
> transmitting, BBB #2's TEC climbs to 128 and the interface goes bus-off. Since
> `setup_can_mode.sh` does not set `restart-ms`, the interface stays bus-off until
> manually reset. Re-run `setup_can_mode.sh normal` on BBB #2 — it brings `can1` down
> and back up cleanly.

---

## Architecture

```
CAN Bus
  │
  ▼
SN65HVD230 transceiver (3.3 V)
  │             │
  │ (CAN RX)    │ (GPIO edge via 1 kΩ)
  ▼             ▼
P9.24         P8.46 (GPIO2_7)
  │             │
can1          GPIO2 bank-A interrupt
(kernel)      │
  │           ▼
  │     PRUSS INTC sysevt 24 → R31[30]
  │           │
  │     PRU0 (IEP timer @ 1 ns/tick)
(kernel)      │
  │           │  DDR ring buffer @ 0x9F000000
  ▼           ▼        (256 × 16-byte event slots, 24-byte header)
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
               ├── tec_rec_poller.py          (ip link show can1, 1 Hz)
               ├── behavioral_monitor.py      (DBC, cycle times, signals)
               └── alert_manager.py           (dedup, cooldowns)
                        │
              ┌─────────┴──────────┐
              ▼                    ▼
         diag_logger.py      FastAPI + WebSocket
         (SQLite WAL)        server.py (5 Hz)
                                   │
                            Browser dashboard
                            (vanilla JS, dark theme)
```

### PRU event types

| Type | Value | Meaning |
|------|-------|---------|
| `SOF` | `0x01` | CAN dominant falling edge captured via GPIO2 interrupt → PRUSS INTC |
| `GLITCH` | `0x02` | *(not generated by INTC firmware — only by legacy direct-GPI firmware)* |
| `DOMINANT_RUNAWAY` | `0x03` | *(not generated by INTC firmware)* |

> **Note:** The INTC-based firmware only generates `SOF` events.  `GLITCH` and
> `DOMINANT_RUNAWAY` classification required continuous pin sampling via `R31[15:0]`,
> which is unavailable because `PRUSS_CFG.GPCFG0` bit 25 is permanently set by the
> 4.19-ti kernel, locking all GPI bits to MII_RT mode.  R31[30] (PRUSS INTC host
> interrupt 0) bypasses this lock.  See [Known Limitations](#known-limitations).

---

## Hardware

### Bill of Materials (~$80)

| Qty | Part | Purpose | ~USD |
|-----|------|---------|------|
| 1 | BeagleBone Black Rev C | AM335x SoC, 2× PRU, 2× DCAN | $55 |
| 1 | SN65HVD230 CAN transceiver module | 3.3 V native — preferred over TJA1050 | $3 |
| 1 | MicroSD 8 GB Class 10 | Debian Buster IoT image with 4.19-ti kernel (or flash eMMC directly) | $8 |
| 1 | 5 V / 2 A supply | BBB power | $8 |
| 1 | DB9 female connector | CAN bus physical interface | $2 |
| 2 | 120 Ω 1/4 W resistor | Bus termination (one per bus end) | $0.50 |
| 1 | 4.7 kΩ resistor | SN65HVD230 RS pin to GND (high-speed mode) | $0.10 |
| 1 | Small breadboard + jumpers | Assembly | $4 |

> **Why SN65HVD230 over TJA1050?** The SN65HVD230 is natively 3.3 V and connects directly to BBB GPIO without level shifting. The TJA1050 requires a 5 V supply and 3.3 V-tolerant I/O consideration.

### Wiring

| Signal | BBB Header Pin | Ball | SN65HVD230 Pin | Notes |
|--------|---------------|------|----------------|-------|
| CAN TX | P9.26 | A14 | TXD (1) | DCAN1 TX, mux mode 2 |
| CAN RX | P9.24 | D15 | RXD (4) | DCAN1 RX, mux mode 2 |
| PRU edge input | **P8.46** | T1 | RXD (4) | Via **1 kΩ** to the P9.24/RXD junction — **see note** |
| 3.3 V | P9.3 or P9.4 | — | VCC (3) | |
| GND | P9.1 or P9.2 | — | GND (2) | Common ground |
| RS pin | — | — | RS (8) | Tie to GND for high-speed mode |
| CANH | DB9 pin 7 | — | CANH (7) | To bus high |
| CANL | DB9 pin 2 | — | CANL (6) | To bus low |

P8.46 (LCD_DATA1, GPIO2_7) is configured as a GPIO input in the kernel.  Its falling-edge
interrupt routes through the **PRUSS INTC** to **R31[30]** in PRU0, giving hardware-level
edge detection without using the GPI path (`R31[15:0]`) which is locked by the kernel.

> **P8.46 boot conflict — 1 kΩ series resistor required.**
> P8.46 boots as LCD_DATA1 (push-pull output, mode 0) before setup_pru.sh reconfigures
> it as a GPIO input.  Without the resistor, the BBB output driver fights the CAN
> transceiver CRXD output during boot.
>
> Add a **1 kΩ resistor** in series between P8.46 and the Y-tap node:
>
> ```
> SN65HVD230 RXD ──┬──── P9.24 (CAN RX / DCAN1)
>                  │
>                1 kΩ
>                  │
>                P8.46 (GPIO2_7 — PRUSS INTC edge input)
> ```
>
> The resistor limits boot-conflict current to ≈ 3 mA and is transparent at 500 kbit/s.

> **Troubleshooting — board won't boot with P8.46 connected.**
> P8.46 is **LCD_DATA1**, which on the AM335x doubles as a **SYSBOOT (boot-mode)
> configuration pin** sampled only during power-on reset.  Before `setup_pru.sh` runs,
> the pin is an LCD_DATA push-pull output; with the jumper attached, the transceiver's
> RX line holds it at a level during the power-on window, which both fights the BBB
> driver and can flip the sampled boot mode — so the board hangs / won't boot.  (This is
> the same conflict that destroyed P8.45's input buffer before the tap moved to P8.46.)
>
> Fixes, in order of preference:
> 1. **Use the correct resistor.** It must be **1 kΩ**, not 100 kΩ.  Verify the installed
>    value first — a wrong/high value is the most common cause.
> 2. **Connect P8.46 after boot.**  Power on with the P8.46 jumper *disconnected*; once
>    Linux is up (services started, pin reconfigured to a GPIO input), connect it.  SYSBOOT
>    is sampled only at power-on reset, so the connection is harmless until the next power
>    cycle.  (Reliable, but not hands-off — avoid for unattended boxes.)
> 3. **Hold the S2 / BOOT button** during power-on to force a fixed boot source.
> 4. If a 1 kΩ still won't boot, relocate the PRU edge tap to a **non-LCD_DATA pin** (any
>    LCD_DATA[0:15] pin has the same SYSBOOT hazard).

### BBB #2 — Traffic / Fault Generator Wiring

A second BBB can generate CAN traffic and physical-layer faults for testing. It uses the same CAN pins but P8.45 is an **output** instead of an input.

#### Diode combiner circuit (required)

P9.26 (DCAN1 TX) and P8.45 (PRU output) both need to drive the transceiver TXD line. They cannot be tied directly together — a push-pull HIGH output will fight a push-pull LOW output. Use two Schottky diodes in a wired-AND configuration with a pull-up:

```
3.3V ──── 4.7 kΩ ────┬──── TXD (SN65HVD230 pin 1)
                     │
P9.26 ──[K ◄── A]───┘
P8.45 ──[K ◄── A]───┘
         D1     D2
      (1N5819 Schottky, ×2)
```

**Diode orientation**: anode at the TXD node, cathode at each BBB pin.

| Component | Value | Notes |
|-----------|-------|-------|
| D1, D2 | 1N5819 (or BAT54) Schottky | Vf ≈ 0.35 V — keeps TXD_low well below SN65HVD230 Vil = 0.8 V |
| R1 | 4.7 kΩ | Pull-up from TXD node to 3.3 V |

**Why Schottky, not 1N4148?** A silicon diode has Vf ≈ 0.65 V. The SN65HVD230 guarantees Vil ≤ 0.8 V, leaving only 0.15 V margin. A Schottky (Vf ≈ 0.35 V) gives 0.45 V margin — much safer.

**How it works:**
- Both P9.26 and P8.45 HIGH → both diodes reverse-biased → pull-up holds TXD at 3.3 V (recessive)
- P9.26 LOW (DCAN sending dominant) → D1 conducts → TXD ≈ 0.35 V (dominant) ✓
- P8.45 LOW (PRU injecting fault) → D2 conducts → TXD ≈ 0.35 V (dominant) ✓
- P9.26 HIGH while P8.45 LOW → D2 conducts; D1 reverse-biased → P9.26 not affected ✓
- Pull-up current when dominant: (3.3 − 0.35) / 4.7 kΩ ≈ 0.6 mA — safe for both pins

#### Wiring table

| Signal | BBB #2 Pin | Connects to |
|--------|-----------|-------------|
| CAN TX | P9.26 | Cathode of D1; anode of D1 to TXD node |
| CAN RX | P9.24 | SN65HVD230 RXD directly |
| PRU fault inject | **P8.45** (output) | Cathode of D2; anode of D2 to TXD node |
| TXD node | — | SN65HVD230 TXD pin 1 + pull-up to 3.3 V |

Connect both transceivers' CANH/CANL lines together (120 Ω termination at each bus end).

See `tools/can_gen/setup_bbb2.sh` and `tools/can_gen/generator.py`.

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
│   ├── BB-DCAN0-00A0.dts          # (unused — can0 is enabled by default in 5.10-ti)
│   ├── BB-PRU0-CAN-TS-00A0.dts    # Phase 1: delete PRUSS pinctrl conflict
│   └── Makefile
├── pru/
│   ├── pru0_timestamp/
│   │   ├── shared_mem.h           # *** Cross-language contract (C ↔ Python) ***
│   │   ├── resource_table.h       # remoteproc resource table (empty — no DDR carveout needed)
│   │   ├── startup.S              # sets sp before main (gcc-pru requires this)
│   │   ├── AM335x_PRU0.ld         # linker script (PRUDMEM at 0x0, DDR ring buffer at 0x9F000000)
│   │   ├── main.c                 # PRU0 IEP timestamp firmware (GPIO→INTC→R31[30])
│   │   └── Makefile
│   ├── pru0_fault_inject/         # BBB #2 fault generator PRU firmware
│   │   ├── shared_mem.h           # fault modes + DDR command interface
│   │   ├── main.c                 # drives P8.45 R30[0] for physical-layer faults
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
│   │   ├── tec_rec_poller.py      # `ip -j link show can1` async poller
│   │   ├── signal_quality_monitor.py  # Glitch/abort counting, DOMINANT_RUNAWAY
│   │   ├── behavioral_monitor.py  # DBC-driven per-message state machine
│   │   ├── alert_manager.py       # Dedup, cooldowns, resolution
│   │   ├── diagnostics_aggregator.py  # Fan-out hub → WebSocket snapshot
│   │   ├── diag_logger.py         # SQLite WAL logger (frames, events, captures, annotations)
│   │   ├── timing_stats.py        # Per-ID interval stats: min/max/mean/σ/p95/p99/jitter
│   │   ├── latency_monitor.py     # Request/response latency; explicit + pattern address pairs
│   │   ├── trigger_capture.py     # Pre/post-trigger capture (200+200 frames, auto re-arm)
│   │   ├── sequence_export.py     # SVG sequence diagram from capture session
│   │   └── server.py              # FastAPI + WebSocket + REST API
│   └── tests/
│       ├── test_correlator.py
│       ├── test_bus_load.py
│       ├── test_error_decoder.py
│       ├── test_behavioral_monitor.py
│       └── test_alert_manager.py
├── data/
│   ├── latency_pairs.example.json # Template — copy to latency_pairs.json and edit
│   └── .gitignore                 # *.db and latency_pairs.json excluded from git
├── frontend/
│   ├── index.html                 # Frames, Diagnostics, Timing, Latency, Trigger, Stats, Graphs, BBB Health tabs
│   ├── style.css                  # Dark theme, CSS custom properties
│   ├── app.js                     # WebSocket client, ring buffer, timing/latency/trigger/health UI
│   ├── graphs.js                  # uPlot diagnostic charts (Graphs tab)
│   └── uPlot.iife.min.js / .css   # vendored uPlot (served from /static/, gitignored)
├── scripts/
│   ├── bootstrap.sh               # first-time setup: clone repo, patch uEnv.txt, reboot
│   ├── install_deps.sh            # post-reboot: apt, pip, build firmware, install services
│   ├── deploy.sh                  # push local changes to a running BBB and reload
│   ├── setup_can.sh               # ip link set can1 up type can bitrate N
│   ├── setup_pru.sh               # P8.46 GPIO interrupt + PRUSS INTC + OCP enable + PRU start
│   └── test_can1_tx.sh            # loopback test: can1 → can0 on same board
├── tools/
│   └── can_gen/
│       ├── generator.py           # BBB #2 CAN traffic + fault generator (12 scenarios)
│       └── setup_bbb2.sh          # full one-shot setup for BBB #2 (generator)
└── systemd/
    ├── pru-loader.service         # Loads PRU firmware via remoteproc
    └── can-sniffer.service        # Starts FastAPI backend
```

> **Critical file:** `pru/pru0_timestamp/shared_mem.h` defines the shared memory layout (24-byte header + 256 × 16-byte ring buffer events) shared between PRU C firmware and Python. Any layout change must be reflected in both `main.c` and `pru_shm.py` simultaneously.

---

## Setup

### Prerequisites

- BeagleBone Black running **Debian 10 Buster** (IoT image) from [rcn-ee.net](https://rcn-ee.net/)
- Kernel `4.19-ti` (verified: `4.19.94-ti-r73`)
- Python 3.7+ (3.7 included in Buster)
- A DBC file describing the target CAN network (for behavioral monitoring)

---

### Phase 0 — OS Baseline and SocketCAN

**Goal:** `can1` up and receiving real frames from the target bus. No PRU involvement.

#### 1. Flash Debian Buster

```bash
# On your workstation — use a Buster (Debian 10) image with 4.19-ti kernel
xzcat bone-debian-10.x-iot-armhf-YYYY-MM-DD-4gb.img.xz | dd of=/dev/mmcblkX bs=4M status=progress
```

Boot, SSH in (USB: `ssh debian@192.168.7.2`, password `temppwd`), set hostname:

```bash
sudo hostnamectl set-hostname can-sniffer
echo "127.0.1.1 can-sniffer" | sudo tee -a /etc/hosts
```

> **Why Buster, not Bookworm (5.10-ti)?** The 5.10-ti and 4.19-ti kernels both set
> `PRUSS_CFG.GPCFG0` bit 25, locking `R31[15:0]` to MII_RT mode.  The firmware works
> around this using the PRUSS INTC path (GPIO2_7 → R31[30]) which is independent of
> GPCFG0.  Either kernel works; Buster is tested and documented here.

> **Note:** `can0` and `can1` are present by default in the 4.19-ti image — no device tree overlay needed to enable the CAN controller.

#### 2. Bring up the CAN interface

```bash
sudo /opt/can_sniffer/scripts/setup_can.sh 500000
ip -details link show can1
# Expected: state UP, bitrate 500000, listen-only in ctrlmodes
```

#### 3. Verify frame reception

```bash
candump can1
# Frames should appear within milliseconds of bus traffic
```

**Acceptance criteria:** `candump` shows frames; no `BUS-ERROR` after 60 s.

---

### Phase 1 — PRU Firmware

**Goal:** PRU0 captures nanosecond SOF timestamps into the DDR ring buffer via GPIO edge
detection through the PRUSS Interrupt Controller.

#### 1. Run bootstrap (first time only)

If you haven't already run `bootstrap.sh`, it clones the repo, patches `uEnv.txt`, and reboots:

```bash
curl -sL https://raw.githubusercontent.com/jpitusb/BBB_CANsniffer/master/scripts/bootstrap.sh | sudo bash
# reboots automatically
```

#### 2. Build and install everything (after the reboot)

```bash
sudo /opt/can_sniffer/scripts/install_deps.sh
# apt packages, pip, PRU firmware, DTS overlay, systemd services
```

Verify:

```bash
systemctl status pru-loader.service can-sniffer.service
# Both should show: active
```

#### 4. Smoke-test the ring buffer

```python
# Run as root on the BBB
import mmap, struct
with open("/dev/mem", "r+b") as f:
    mm = mmap.mmap(f.fileno(), 0x2000, offset=0x9F000000)
magic, widx = struct.unpack_from("<II", mm, 0)
print(f"magic=0x{magic:08X}  write_idx={widx}")
# Expected: magic=0xCAFE1234  write_idx=N (increasing with bus traffic)
```

---

### Phase 2 — Python Backend

**Goal:** Python correlates PRU timestamps with SocketCAN frames and serves data over WebSocket.

#### 1. Install Python dependencies

```bash
pip3 install --break-system-packages '/opt/can_sniffer/backend[dev]'
```

This is handled automatically by `install_deps.sh` (see Phase 1).

#### 2. Place your DBC file

Copy your network DBC file to the BBB and set the path in the service (see [Configuration](#configuration)).

#### 3. Start the backend

The systemd service starts automatically on boot after Phase 1 setup. To start manually:

```bash
sudo systemctl start can-sniffer.service
```

Or run directly for development (adjust PYTHONPATH to your user's site-packages):

```bash
PY=$(python3 --version | grep -oP '3\.\d+')
sudo PYTHONPATH="$HOME/.local/lib/python${PY}/site-packages" \
    python3 -m can_sniffer.server
```

#### 4. Open the dashboard

Navigate to `http://<BBB-IP>:8000`. Frames appear within 200 ms of bus traffic.

---

### Phase 3 — Browser Dashboard

The frontend is served automatically by the FastAPI backend from `frontend/`. No separate build step required.

**Panels:**

| Tab | Panel | Contents |
|-----|-------|----------|
| Frames | Frame table | PRU timestamp (ns), kernel timestamp (s), Arb ID, DLC, data bytes, per-ID delta time (µs) |
| Frames | Bus load bar | Rolling 1-second utilization; colors green → amber → red |
| Timing | Stats table | Per-ID: count, fps, min/max/mean/σ/p95 interval (ms), jitter RMS; highlights when jitter > 10% of mean |
| Latency | Latency table | Per configured pair: count, min/max/mean/σ/last (µs) |
| Latency | Edit Pairs | Live editor for `latency_pairs.json`; changes take effect immediately |
| Trigger | Arm controls | Condition selector (arb_id / error frame / bus load / manual), arm/disarm/fire buttons, capture list |
| Trigger | Captures list | Past captures with timestamp, condition, frame count; JSON and SVG download links |
| Diagnostics | Bus health | TEC / REC live, bus state badge, error frames/sec |
| Diagnostics | Protocol errors | Session counts: bit / stuff / CRC / form / ACK errors |
| Diagnostics | Signal quality | Glitches/s, aborted frames/s, dominant runaway status |
| Diagnostics | Missing messages | Per-ID overdue time vs. DBC cycle time |
| Diagnostics | Alert feed | Active CRITICAL / WARN / INFO alerts with age |
| Stats | Summary | Total frames, unique IDs, error events, aborted frames |
| Graphs | Diagnostic charts | uPlot: bus-load timeline, TEC/REC trend, error rate, latency trend (per pair), frame-activity heatmap, per-ID interval histogram, PRU-vs-kernel Δ histogram |
| BBB Health | System monitor | CPU %, memory, CPU temperature, load average, uptime, disk usage (color-coded bars) |

**Controls:**

- **Filter ID** — hex prefix match, applied client-side against a 10 000-frame ring buffer
- **Pause / Resume** — halts table updates without disconnecting WebSocket
- **Clear** — empties the frame ring buffer and DOM table
- **Double-click a frame row** — prompts for a text note; stored in SQLite with the frame's timestamp and arb_id
- **Reset Stats** (Latency tab) — clears all timing and latency accumulators

---

### Phase 4 — PRU1 Bit-Bang CAN (Stretch)

Placeholder firmware in `pru/pru1_bitbang/main.c`. Intended for a second CAN bus channel at ≤ 250 kbit/s using PRU1 GPI input and a second SN65HVD230. Note: P8.46 is occupied by the GPIO edge detection path for BBB #1; a different pin would need to be selected. See roadmap below.

---

## Diagnostics Deep Dive

### Timing Statistics

Per-ID interval statistics are computed from a rolling window of the last 100 inter-frame intervals:

| Metric | Description |
|--------|-------------|
| `interval_mean_ms` | Mean inter-frame interval |
| `interval_std_ms` | Standard deviation (= jitter RMS) |
| `interval_p95_ms` | 95th-percentile interval |
| `interval_p99_ms` | 99th-percentile interval |
| `frames_per_sec` | Derived from mean interval |

Rows where `jitter_rms > interval_mean × 10%` are highlighted amber in the Timing tab.

### Latency Monitoring

Latency is measured from the kernel timestamp of the request frame to the kernel timestamp of the matching response frame. Results are in microseconds.

Pattern pairs automatically match any address where the lower byte is the same node ID — `0x160 → 0x060`, `0x161 → 0x061`, etc. See [Configuration → Latency address pairs](#latency-address-pairs).

### Pre/Post-Trigger Capture

The trigger system maintains a rolling 200-frame pre-trigger buffer at all times. On trigger:
1. Pre-buffer snapshot (last 200 frames before trigger) is frozen
2. Next 200 frames collected as post-trigger
3. Session saved to SQLite and listed in the Trigger tab
4. Trigger auto-re-arms for the next event

Trigger conditions:
| Condition | Arms on |
|-----------|---------|
| `arb_id` | Specific arb ID seen |
| `error_frame` | Any SocketCAN error frame |
| `bus_load` | Bus utilisation ≥ threshold |
| `manual` | Fire button / `POST /api/trigger/fire` |

Each capture is downloadable as **JSON** (raw frame list) or **SVG** (sequence diagram with swimlanes, latency arrows, and trigger-point marker).

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

TEC and REC are read from `data[6:8]` on controller error frames, and refreshed at 1 Hz via `ip -j -d link show can1` (P9.24/P9.26).

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

Database path defaults to `/opt/can_sniffer/data/diagnostics.db` (override with `CAN_SNIFFER_DB`). Tables:

| Table | Retention | Contents |
|-------|-----------|---------|
| `can_frames` | 7 days | Every received frame (ts, can_id, dlc, data, pru_ts_ns, is_aborted) |
| `error_events` | 7 days | SocketCAN error frames + TEC/REC |
| `pru_events` | 30 days | All PRU ring buffer events (SOF, GLITCH, RUNAWAY) |
| `behavioral_alerts` | 90 days | All fired alerts with severity and resolution state |
| `bus_state_log` | 30 days | TEC/REC/state snapshots at 1 Hz |
| `captures` | 30 days | Pre/post-trigger capture sessions (full frame JSON) |
| `frame_annotations` | 90 days | User notes attached to specific frames by timestamp + arb_id |

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

### Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `CAN_SNIFFER_DB` | `/opt/can_sniffer/data/diagnostics.db` | SQLite database path |

Everything else (`can1` interface, 500 kbit/s bitrate, port 8000) is currently hardcoded.
The CAN bitrate thresholds are compiled into the PRU firmware (`shared_mem.h`); change them
there and rebuild before changing the bitrate passed to `setup_can.sh`.

### Latency address pairs

Edit `/opt/can_sniffer/data/latency_pairs.json` (or use the **Edit Pairs** button in the Latency tab). Two entry types:

```json
[
  {
    "type": "explicit",
    "request_id": "0x601",
    "response_id": "0x581",
    "label": "SDO Node 1"
  },
  {
    "type": "pattern",
    "request_base": "0x100",
    "response_base": "0x000",
    "label_template": "Device 0x{node_id:02X}"
  }
]
```

**Explicit pair** — measures latency between two specific arb IDs.

**Pattern pair** — auto-matches any address where the lower byte (node ID) is the same:
`request_base | node_id` → `response_base | node_id`. For example, `0x100` base with `0x060` node matches `0x160 → 0x060`, `0x161 → 0x061`, etc. `node_mask` is always `0xFF` (lower byte).

A template file is at `data/latency_pairs.example.json`. Changes take effect immediately via PUT `/api/latency/pairs` — no service restart needed.

---

## Development

### Running tests on BBB

```bash
cd /opt/can_sniffer/backend
python3.11 -m pytest tests/ -v
# 22 passed
```

Tests use synthetic timestamps and in-memory state — no CAN hardware required.

### Deploying changes to the BBB

```bash
# From your workstation — rsyncs repo, reinstalls Python package, restarts services
BBB_HOST=10.183.184.161 BBB_USER=lauren ./scripts/deploy.sh

# Or manually on the BBB
cd /opt/can_sniffer && git pull
pip3 install --break-system-packages '/opt/can_sniffer/backend'
sudo systemctl restart pru-loader can-sniffer
```

### Rebuilding PRU firmware after changes

```bash
cd /opt/can_sniffer/pru/pru0_timestamp
make clean && make
sudo cp am335x-pru0-fw /lib/firmware/
sudo systemctl restart pru-loader.service
```

### Changing CAN bitrate

The bit-timing thresholds in `shared_mem.h` are compiled into the PRU firmware:

```c
#define GLITCH_THRESHOLD_COUNTS  200U   /* 1000 ns = 0.5 bit at 500 kbit/s */
#define SOF_MAX_COUNTS          4000U   /* 20000 ns = 10 bits               */
#define IFS_COUNTS              1200U   /* 6000 ns = 3 bits (IFS)           */
```

If you change the bus bitrate, recalculate these constants (IEP ticks = time_ns / 1, since 1 tick = 1 ns at DEFAULT_INC=5) and rebuild the PRU firmware before restarting. The Python backend reads the bitrate from `CAN_BITRATE` at startup and does not need recompilation.

### `shared_mem.h` is the cross-language contract

`pru_shm_t` is the full shared memory struct. Python reads it via `/dev/mem` mmap.
Any layout change must be reflected in both `main.c` and `pru_shm.py`.

**Header** (24 bytes at `PRU_SHM_ARM_ADDR = 0x9F000000`):

```
Offset  Size  Field
     0     4  magic           (0xCAFE1234; Python checks this at startup)
     4     4  write_idx       (PRU increments; Python polls; mask with 0xFF for slot)
     8     4  _pad            (reserved)
    12     4  _pru_prev_iep   (PRU-private: last IEP sample for rollover tracking)
    16     8  _pru_rollover_ns (PRU-private: accumulated rollover ns)
```

**Ring buffer** (256 × 16-byte events, starting at offset 24):

```
Offset  Size  Field
     0     1  type       (0x01 SOF, 0x02 GLITCH, 0x03 DOMINANT_RUNAWAY)
     1     1  flags      (bit 0: IEP rollover since previous entry)
     2     2  seq        (monotonic uint16, wraps at 65535)
     4     8  t_fall_ns  (uint64 monotonic ns since PRU start; Python adds epoch_offset)
    12     4  pulse_ns   (0 for SOF — pulse classification not available with INTC path)
```

Python struct: `_HDR_MAGIC_WIDX = struct.Struct("<II")` (reads first 8 bytes), event offset = 24.

> The `_pru_prev_iep` and `_pru_rollover_ns` fields exist because gcc-pru SBBO always
> uses ARM physical addresses — C static variables at PRUDMEM origin 0x0 hit boot ROM
> and writes are silently dropped. These fields put rollover state in DDR where SBBO works.
>
> **IEP tick rate:** `DEFAULT_INC = 5` at 200 MHz = 1 ns per tick (1 GHz effective).
> Rollover: 2³² ticks × 1 ns = ~4.29 s.  `t_fall_ns` accumulates correctly across
> multiple rollovers via `_pru_rollover_ns`.

---

## REST API

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/` | Dashboard HTML |
| `GET` | `/api/latency/pairs` | Current latency pair config |
| `PUT` | `/api/latency/pairs` | Update latency pairs (JSON body); takes effect immediately |
| `POST` | `/api/timing/reset` | Clear all timing statistics |
| `POST` | `/api/trigger/arm` | Arm trigger; body: `{"type":"arb_id","arb_id":352}` |
| `POST` | `/api/trigger/disarm` | Disarm trigger |
| `POST` | `/api/trigger/fire` | Manually fire trigger (must be armed) |
| `GET` | `/api/captures` | List capture session summaries |
| `GET` | `/api/captures/{id}` | Full capture JSON (pre + post frames) |
| `GET` | `/api/captures/{id}/svg` | SVG sequence diagram for capture |
| `POST` | `/api/frames/annotate` | Annotate a frame; body: `{"kernel_ts":…,"arb_id":…,"note":"…"}` |

---

## Bus Impact — Is the Sniffer Invisible?

**Short answer:** With `listen-only on` (the default in `setup_can.sh`), the sniffer is electrically passive and invisible to all other bus nodes. Without it, the CAN controller participates in the protocol.

### Three interaction mechanisms

| Mechanism | Default mode | Listen-only mode |
|-----------|-------------|-----------------|
| **ACK bit** | CAN controller acknowledges every correctly received frame | No ACK sent — controller is fully passive |
| **Error frames** | CAN controller transmits a 6-bit error flag if it detects a bus error, aborting the in-progress frame | No error frames sent |
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

- **PRUSS GPI R31[15:0] locked by kernel:** The 4.19-ti (and 5.10-ti) kernel sets
  `PRUSS_CFG.GPCFG0` bit 25 during PRUSS initialization, locking all 16 GPI bits
  (`R31[15:0]`) to MII_RT mode.  Writes to this bit — from either ARM or PRU — are
  silently discarded.  The firmware works around this by routing the CAN RX edge through
  the PRUSS Interrupt Controller (`GPIO2_7 → GPIO2 bank-A interrupt → sysevt 24 →
  R31[30]`).  R31[30] is the PRUSS INTC host interrupt 0 output and is unaffected by
  GPCFG0.

- **INTC path only generates SOF events — no GLITCH or DOMINANT_RUNAWAY:** The INTC
  approach detects GPIO falling edges but cannot classify pulse width (which requires
  continuous `R31` sampling).  `GLITCH` and `DOMINANT_RUNAWAY` events are defined in the
  protocol but never emitted; the signal quality diagnostics panel will show zeros.

- **Partial SOF timestamp coverage; 10 ms blind period:** After each captured SOF the
  firmware ignores edges for a blind period (`BLIND_COUNTS` in `main.c`, currently
  10 ms = ~100 captures/sec max) before re-arming.  This exists because P8.46 re-asserts
  the PRUSS INTC line essentially every blind period even on a near-idle bus, so a short
  blind period pegs the PRU at its ceiling and floods the single-core ARM backend with
  phantom events.  At 10 ms the PRU contributes at most ~100 nanosecond timestamps/sec;
  on a faster bus the remaining frames carry only the kernel microsecond timestamp.
  Lower `BLIND_COUNTS` (toward `1000000u` = 1 ms) for higher coverage **only** if you have
  CPU headroom — see the performance note below.

- **CPU scales with frame rate (~0.38%/frame on the AM335x):** The per-frame correlation
  and diagnostics pipeline is Python on a 1 GHz single-core Cortex-A8.  Idle baseline is
  ~20%; at the ~120 frame/sec command/response rate the backend uses ~60-65% of the core.
  `bus.recv()` itself is cheap (~8%) and the WebSocket is negligible at that rate — the
  cost is the aggregate per-frame fan-out.  There is no single hot spot to optimise; the
  board has headroom at typical command/response rates but is not suited to sustained
  multi-thousand-frame/sec buses.

- **IEP timer tick = 1 ns (DEFAULT_INC = 5):** The AM335x IEP increments by 5 per
  200 MHz PRU clock cycle, giving 1 ns effective resolution (not 5 ns as documented in
  older TI materials).  Rollover occurs every ~4.29 s (2³² ns).  The firmware tracks
  rollovers; `t_fall_ns` in each event is monotonically increasing.  Accuracy degrades
  by ±50 ppm (crystal tolerance); restart the backend to recalibrate.

- **P8.46 GPIO interrupt fires to both PRU (via INTC) and ARM Linux:** Without the sysfs
  `echo falling > edge` step, the GPIO bank interrupt reaches the PRUSS INTC hardware
  path with no ARM handler registered.  If `echo falling > edge` is used, the ARM also
  gets an interrupt handler that fires at the CAN edge rate, consuming significant CPU.
  `setup_pru.sh` configures the GPIO exclusively via `/dev/mem` to avoid registering the
  ARM handler.

- **P8.46 is a SYSBOOT pin — can block boot:** P8.46 (LCD_DATA1) is sampled as a boot-mode
  config pin at power-on reset.  With the edge-tap jumper attached, the transceiver can
  hold it at a level that corrupts the sampled boot mode and the board won't boot.  Use
  the **1 kΩ** series resistor (not 100 kΩ); if it still won't boot, connect P8.46 after
  boot, hold S2 at power-on, or move the tap to a non-LCD_DATA pin.  See the
  *Troubleshooting* note under [Hardware → Wiring](#wiring).

- **SocketCAN error frames do not report CRC errors as a dedicated class:** CRC errors
  are inferred from `data[3]` (protocol violation location byte) when `CAN_ERR_PROT` is
  set. This is a Linux kernel limitation, not a firmware one.
- **Aborted frame detection has a 5 ms latency:** By design — the timeout must exceed worst-case Linux socket delivery latency. Isolated aborted frames appear in the dashboard within 5–6 ms of the bus event.
- **Web UI has no authentication:** The FastAPI server binds to `0.0.0.0:8000` with no access control. Use an SSH tunnel or restrict binding to `127.0.0.1` on shared networks.
- **Single CAN channel:** Only one CAN interface (`can1`, P9.24/P9.26) is currently implemented. PRU1 bit-bang second channel is planned (see roadmap).
- **DBC signals with no min/max defined:** `cantools` returns `None` for `signal.minimum` / `signal.maximum`; range checking is silently skipped for those signals.

---

## Roadmap

- [ ] **Config file** — YAML/TOML config for bitrate, DBC path, DB path, ADC tap enable
- [ ] **Historical query API** — REST endpoints for querying the SQLite DB (`/api/frames`, `/api/alerts`, `/api/errors`) for post-hoc analysis
- [ ] **ADC tap** — optional `adc_reader.py` reading `/sys/bus/iio/devices/iio:device0/` for bus DC health metrics
- [ ] **PRU1 bit-bang CAN** — software CAN receiver for a second channel at ≤ 250 kbit/s (`pru/pru1_bitbang/`), multiplexed in the WebSocket JSON as `"channel": 1`
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
