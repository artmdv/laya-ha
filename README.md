# ⚡ Laya System-1 Conversation for Home Assistant

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg?style=for-the-badge)](https://github.com/hacs/default)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg?style=for-the-badge)](https://opensource.org/licenses/MIT)
[![Home Assistant](https://img.shields.io/badge/Home%20Assistant-2024.1+-blue.svg?style=for-the-badge)](https://www.home-assistant.io/)

A blazing-fast, non-autoregressive **System-1 conversation agent** for Home Assistant, powered by [Laya](https://github.com/NandhaKishorM/laya).

Instead of waiting 400–1200ms for a generative LLM to slowly spit out tokens and risk hallucinations, **Laya System-1** classifies your voice command into precise actions and target entities in a single forward pass (**~30 milliseconds**).

---

## 🚀 Key Advantages Over Traditional LLMs

| Feature | Generative LLMs (Ollama / Qwen / GPT) | Laya System-1 Conversation |
| :--- | :--- | :--- |
| **Inference Latency** | 400 ms – 1500 ms (token generation) | **~30 ms – 50 ms (single pass)** |
| **Hallucinations** | Can invent fake devices, apology text, or code | **Mathematically impossible** (closed answer space) |
| **Context Overhead** | Requires serializing thousands of entity states | **Zero state bloat** (sends only action/target candidates) |
| **Resource Usage** | 2 GB – 8 GB VRAM | **~300 MB RAM** (runs easily on low-power CPU) |
| **Multilingual** | Often degrades on Baltic/smaller languages | **Calibrated multilingual routing** (EN, LT, and 100+ languages) |

---

## 🛠️ Features

* **Dynamic Catalog Discovery**: Automatically scans all registered Home Assistant **Areas** (rooms) and **Entities** (lights, switches, covers, vacuums, media players, fans, climate).
* **Area-Level and Device-Level Control**: Understands both room-level commands (*"turn off kitchen lights"*) and specific device commands (*"start Roborock"*).
* **Calibrated Confidence Threshold**: Configurable confidence cutoff (e.g. `0.50`). If an utterance is ambiguous, Laya safely declines rather than triggering an unintended device.
* **100% Configurable via UI**: Native Config Flow and Options Flow—no YAML configuration required.
* **Device Control & Status Queries**: Not only controls devices, but also answers status questions (*"What is the temperature outside?"*, *"Is the gate closed?"*, *"Kokia lauko temperatūra?"*, *"Ar vartai uždaryti?"*).
* **Multilingual Feedback**: Built-in support for English, Lithuanian, and extensible to any language with concise or verbose speech responses.

---

## 📦 1. Running Laya (Docker / Dockge)

Laya runs locally as a lightweight inference microservice. You can run it on Unraid, Docker Compose, or Dockge:

```yaml
version: "3.8"
services:
  laya:
    image: python:3.11-slim
    container_name: laya-system1
    restart: unless-stopped
    ports:
      - "12003:8000"
    environment:
      - LAYA_DEVICE=cpu
      - LAYA_PRELOAD=1
      - TMPDIR=/cache/tmp
      - PIP_CACHE_DIR=/cache/pip
    volumes:
      - laya_cache:/cache
      - laya_models:/root/.cache/huggingface
    command: >
      sh -c "mkdir -p /cache/tmp /cache/pip && pip install --no-cache-dir 'laya[serve]' && laya-serve --host 0.0.0.0 --port 8000"

volumes:
  laya_cache:
  laya_models:
```

Test that your server is running:
```bash
curl http://<YOUR_SERVER_IP>:12003/health
```

---

## 📥 2. Installation in Home Assistant

### Option A: Via HACS (Recommended)

1. Open **HACS** in your Home Assistant.
2. Click the three dots (top right) $\rightarrow$ **Custom repositories**.
3. Paste this repository URL:
   * **URL**: `https://github.com/<your-username>/laya-ha`
   * **Type**: `Integration`
4. Click **Add**, find **Laya System-1 Conversation**, and click **Download**.
5. Restart Home Assistant.

### Option B: Manual Installation

1. Copy the `custom_components/laya` folder into your Home Assistant `<config_dir>/custom_components/` directory.
2. Restart Home Assistant.

---

## ⚙️ 3. Configuration

1. In Home Assistant, navigate to **Settings** $\rightarrow$ **Devices & Services**.
2. Click **+ Add Integration** $\rightarrow$ Search for **Laya System-1**.
3. Enter your Laya server URL (e.g. `http://192.168.0.159:12003`).
4. *(Optional)* Enter an API key if you enabled authentication.
5. Click **Submit**.

### Options & Customization

Click **Configure** on the integration card to customize:
* **Confidence Threshold**: Set the minimum probability required (default `0.50`).
* **Exposed Domains**: Select which device types Laya can command (e.g. lights, switches, vacuums, covers, fans).
* **Confirmation Style**:
  * **Concise**: e.g., *"Turned off"* / *"Išjungta"* (Fastest, zero fluff).
  * **Verbose**: e.g., *"Turned off Living Room Light"*.
* **Timeout**: Request timeout in seconds (default `3.0`).

---

## 🎙️ 4. Activate in Your Voice Pipeline

1. Go to **Settings** $\rightarrow$ **Voice Assistants**.
2. Open your voice pipeline (e.g. **Home Assistant Cloud**, **Wyoming / Vėtra**, or your default assistant).
3. In the **Conversation Agent** dropdown, change from Home Assistant / Ollama to:  
   👉 **`Laya System-1`**
4. Save!

Now speak to your voice satellite (e.g. Atom Echo):
> *"Turn off the living room lights"*  
> *"Išjunk virtuvės šviesą"*  
> *"What is the temperature outside?"*  
> *"Kokia lauko temperatūra?"*  
> *"Is the front gate closed?"*  
> *"Ar kiemo vartai uždaryti?"*  
> *"Start the vacuum"*  
> *"Paleisk siurblį"*

Execution is instant, deterministic, and accurate!

---

## 🧪 Testing

Run the test suite locally:

```bash
python -m unittest discover -s tests
```

---

## 📄 License

Released under the [MIT License](LICENSE).
