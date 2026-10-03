# ⚡ System-1 Conversation for Home Assistant
### Ultra-fast, Non-Autoregressive Smart Home Agent (Clef / Laya / Jev)

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg?style=for-the-badge)](https://github.com/hacs/default)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg?style=for-the-badge)](https://opensource.org/licenses/MIT)
[![Home Assistant](https://img.shields.io/badge/Home%20Assistant-2024.1+-blue.svg?style=for-the-badge)](https://www.home-assistant.io/)

A universal, non-autoregressive **System-1 conversation agent** for Home Assistant, powered by modern decision models like **Cloudflare Clef / Clef-flash**, **Laya**, and **TypeSafe Jev**.

Instead of waiting 400–1500ms for a generative LLM to slowly generate tokens and risk hallucinations, **System-1 models** classify your voice command into precise actions and target entities in a single forward pass (**~30–40 milliseconds**).

---

## 🚀 Why System-1 Decision Models Over Generative LLMs?

| Feature | Generative LLMs (Ollama / Qwen / GPT) | System-1 Conversation (Clef / Laya) |
| :--- | :--- | :--- |
| **Inference Latency** | 400 ms – 1500 ms (autoregressive token loop) | **~30 ms – 40 ms (single forward pass)** |
| **Hallucinations** | Can invent fake devices, hallucinate apology text, or malformed JSON | **Mathematically impossible** (closed candidate answer space) |
| **Context Overhead** | Requires serializing thousands of entity states into prompt tokens | **Zero state bloat** (evaluates candidates via typed schema) |
| **Resource Usage** | 4 GB – 16 GB VRAM | **Lightweight local execution** or edge server |
| **Multilingual** | Degrades on Baltic/smaller languages | **Superior multilingual routing** (Lithuanian, English, 100+ languages) |

---

## 🛠️ Features

* **Universal `/v1/systemone` Client**: Connects to **Clef** (local vLLM or Ollama), **Laya** (`laya-serve`), or any compatible System-1 engine.
* **Dynamic Catalog Discovery**: Automatically scans all registered Home Assistant **Areas** (rooms) and **Entities** (lights, switches, covers, vacuums, media players, fans, climate).
* **Area-Level and Device-Level Control**: Understands both room-level commands (*"turn off kitchen lights"*) and specific device commands (*"start Roborock"*).
* **Calibrated Confidence Threshold**: Configurable cutoff (e.g. `0.50`). If an utterance is ambiguous, System-1 safely declines rather than triggering an unintended device.
* **100% Configurable via UI**: Native Config Flow and Options Flow with engine and model selection—no YAML required.
* **Device Control & Status Queries**: Not only controls devices, but also answers status questions (*"What is the temperature outside?"*, *"Is the gate closed?"*, *"Kokia lauko temperatūra?"*, *"Ar vartai uždaryti?"*).
* **Multilingual Feedback**: Built-in native feedback for English and Lithuanian, extensible with concise or verbose response styles.

---

## 📦 1. Running Clef Locally

### Option A: Via Ollama (Quickstart)
Ollama (v0.35.1+) natively runs Clef and exposes the decision endpoint:

```bash
# Run Clef-flash (9B, ultra-low latency ~39ms)
ollama run cloudflare/clef-flash

# Or run Clef (27B, highest precision)
ollama run cloudflare/clef
```

### Option B: Via vLLM (Docker Compose with GPU)
For lowest latency and highest throughput with PagedAttention:

```yaml
version: "3.8"
services:
  clef:
    image: vllm/vllm-openai:latest
    container_name: clef-system1
    restart: unless-stopped
    ports:
      - "8000:8000"
    command: >
      --model cloudflare/clef-flash
      --port 8000
      --gpu-memory-utilization 0.90
      --max-model-len 2048
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: all
              capabilities: [gpu]
```

### Option C: Running Laya (CPU Microservice)
```yaml
version: "3.8"
services:
  laya:
    image: python:3.11-slim
    container_name: laya-system1
    restart: unless-stopped
    ports:
      - "12003:8000"
    command: >
      sh -c "pip install --no-cache-dir 'laya[serve]' && laya-serve --host 0.0.0.0 --port 8000"
```

---

## 📥 2. Installation in Home Assistant

### Option A: Via HACS (Recommended)

1. Open **HACS** in Home Assistant.
2. Click the three dots (top right) $\rightarrow$ **Custom repositories**.
3. Add:
   * **URL**: `https://github.com/artmdv/laya-ha`
   * **Type**: `Integration`
4. Search for **System-1 Conversation** and click **Download**.
5. Restart Home Assistant.

### Option B: Manual Installation

1. Copy the `custom_components/system1` folder into your Home Assistant `<config_dir>/custom_components/` directory.
2. Restart Home Assistant.

---

## ⚙️ 3. Configuration

1. In Home Assistant: **Settings** $\rightarrow$ **Devices & Services** $\rightarrow$ **Add Integration**.
2. Search for **System-1 Conversation**.
3. Select your engine:
   * **Clef (Local vLLM / Ollama)** (Default URL: `http://localhost:8000`)
   * **Laya (Local laya-serve)** (Default URL: `http://localhost:12003`)
   * **Generic System-1** (Custom `/v1/systemone` URL)
4. Enter model (e.g. `cloudflare/clef-flash` or `cloudflare/clef`).
5. Click **Submit**.

### Assign as Default Assist Voice Agent
1. Go to **Settings** $\rightarrow$ **Voice assistants**.
2. Click your assistant profile (e.g. **Home Assistant Cloud** or **Local**).
3. Under **Conversation agent**, select **System-1 Conversation**.

---

## 📄 License
MIT License.
