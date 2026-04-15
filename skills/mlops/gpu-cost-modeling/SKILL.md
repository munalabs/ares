---
name: gpu-cost-modeling
category: mlops
description: "Model and compare costs for cloud API vs local GPU inference. Calculates monthly costs across scenarios: cloud-only, always-on GPU, on-demand GPU, and hybrid. Includes Hetzner GPU pricing, VRAM-to-model fit mapping, and multi-purpose utilization analysis."
version: 1.0.0
metadata:
  hermes:
    tags: [gpu, cost, inference, hetzner, vllm, local-models, cloud-api, pricing]
---

# GPU Cost Modeling — Cloud vs Local Inference

## When to Use

- User asks "how much would it cost to run models locally?"
- Evaluating cloud API vs self-hosted GPU for any workload
- Sizing a Hetzner (or similar) GPU server for multi-purpose use
- Comparing on-demand vs always-on GPU economics
- Building a business case for local inference (privacy, cost, independence)

## Key Principle

The decision is never just "cloud vs local" — it's a matrix of:
1. **Workload volume** (tokens/month)
2. **Privacy requirements** (can code/data leave your infra?)
3. **Quality threshold** (does a 32B model match frontier quality?)
4. **Multi-purpose value** (what else can the GPU do when idle?)

## Step 1: Estimate Workload

```python
# Template — adjust per use case
WORKLOAD = {
    "tasks_per_month": 160,          # PRs, reviews, queries, etc.
    "input_tokens_per_task": 40_000,  # System prompt + context + input
    "output_tokens_per_task": 13_000, # Model response
    # Derived
    "monthly_input": 160 * 40_000,   # 6.4M
    "monthly_output": 160 * 13_000,  # 2.1M
}
```

**Common workload profiles:**

| Use Case | Input/task | Output/task | Tasks/mo |
|---|---:|---:|---:|
| PR security review (2-pass) | 40K | 13K | 100-500 |
| Code completion (IDE) | 2K | 500 | 10,000+ |
| Chat assistant | 5K | 2K | 1,000+ |
| Document analysis | 20K | 5K | 200-500 |
| Pentest engagement | 50K | 15K | 50-100 |

## Step 2: Cloud API Pricing

```python
# USD per 1M tokens — update when prices change (last: 2026-04)
CLOUD_PRICING = {
    "DeepSeek V3 (API)":  {"input": 0.27,  "output": 1.10},
    "GPT-4.1-mini":       {"input": 0.40,  "output": 1.60},
    "GPT-4.1":            {"input": 2.00,  "output": 8.00},
    "Claude Sonnet":      {"input": 3.00,  "output": 15.00},
    "Claude Opus":        {"input": 15.00, "output": 75.00},
    "Gemini 2.5 Pro":     {"input": 1.25,  "output": 10.00},
    "Gemini 2.5 Flash":   {"input": 0.15,  "output": 0.60},
}

def cloud_monthly_cost(model, monthly_input, monthly_output):
    p = CLOUD_PRICING[model]
    return (monthly_input / 1e6 * p["input"]) + (monthly_output / 1e6 * p["output"])
```

## Step 3: Hetzner GPU Options

### Cloud Servers (hourly, on-demand, auto-provisioned)

| Type | GPU | VRAM | €/hr | Best for |
|---|---|---:|---:|---|
| GEX44 | RTX 4000 Ada SFF | 20 GB | €0.548 | 32B Q4 models |
| GEX130 | L40S | 48 GB | €1.49 | 70B Q4 or 235B MoE |

### Dedicated Servers (monthly, bare metal, always-on)

| Type | GPU | VRAM | €/mo | Best for |
|---|---|---:|---:|---|
| EX44-GPU | RTX 4000 Ada | 20 GB | €121 | Single 32B model |
| EX130-GPU | L40S | 48 GB | €304 | Multi-model, frontier MoE |

**Rule of thumb:** Dedicated breaks even vs on-demand at ~220h/mo usage (GEX44) or ~204h/mo (GEX130). Below that, on-demand wins.

### Persistent Storage

| Resource | Cost | Purpose |
|---|---|---|
| Volume (model weights) | €0.052/GB/mo | Attach on boot, skip re-download |
| Snapshot (vLLM image) | €0.012/GB/mo | Fast boot from pre-configured image |

## Step 4: VRAM-to-Model Mapping

```
VRAM formula: params_B × (bits / 8) + 2GB overhead + KV cache

20GB fits:
  - 32B Q4_K_M (~18GB) ← one model only
  - 16B FP16 (~14GB) + 8B Q4 (~6GB) simultaneously
  - 8B FP16 (~16GB) with room for KV cache

48GB fits:
  - 235B MoE Q4 (~45GB, 22B active) ← near-frontier
  - 70B Q4_K_M (~37GB)
  - 32B FP16 (~34GB) + 8B FP16 (~16GB) simultaneously
  - 72B Q4_K_M (~38GB)

96GB fits (2× GPU with tensor parallelism):
  - 405B Q2 (~90GB)
  - 235B MoE FP16 (~60GB)
  - Multiple 70B models
```

## Step 5: On-Demand GPU Cost Calculation

```python
def ondemand_monthly_cost(tasks_per_month, input_per_task, output_per_task,
                          prefill_tps, output_tps, hourly_rate,
                          base_infra=6, cold_start_min=3, idle_min=10,
                          tasks_per_session=5):
    """
    prefill_tps: input token processing speed (tok/s)
    output_tps: output generation speed (tok/s)
    """
    # Time per task
    prefill_sec = input_per_task / prefill_tps
    gen_sec = output_per_task / output_tps
    task_min = (prefill_sec + gen_sec) / 60

    # Monthly GPU hours
    review_hours = (task_min * tasks_per_month) / 60
    sessions = tasks_per_month / tasks_per_session
    cold_start_hours = (sessions * cold_start_min) / 60
    idle_hours = (sessions * idle_min) / 60
    total_gpu_hours = review_hours + cold_start_hours + idle_hours

    gpu_cost = total_gpu_hours * hourly_rate
    return {
        "task_minutes": task_min,
        "gpu_hours": total_gpu_hours,
        "gpu_cost": gpu_cost,
        "total": gpu_cost + base_infra,
        "per_task": (gpu_cost + base_infra) / tasks_per_month,
    }
```

**Reference speeds (Qwen2.5-Coder-32B Q4_K_M):**

| GPU | Prefill (tok/s) | Output (tok/s) |
|---|---:|---:|
| RTX 4000 Ada (20GB) | ~1200 | ~30 |
| RTX 4090 (24GB) | ~1500 | ~40 |
| L40S (48GB) | ~2500 | ~55 |
| A100 80GB | ~4000 | ~70 |

## Step 6: Multi-Purpose Utilization (Always-On)

When evaluating always-on GPU, list ALL workloads it replaces:

```markdown
| Service replaced | Current cost | With local GPU | Savings |
|---|---:|---:|---:|
| PR security reviews (API) | €51/mo | €0 | €51 |
| GitHub Copilot (N devs × €19) | €57/mo | €0 | €57 |
| Chat API calls (daily) | ~€80/mo | ~€20/mo hybrid | €60 |
| Pentest API spend | ~€50/mo | €0 | €50 |
| On-demand GPU rentals | ~€30/mo | €0 | €30 |
| TOTAL REPLACED | €268/mo | | €248 |
| GPU server cost | | €304/mo | |
| NET DELTA | | | +€56/mo |
```

The "net delta" makes the real business case — it's not €304/mo, it's €56/mo for unlimited private inference.

## Step 7: Present the Comparison

Always show all scenarios in one table:

```
Scenario                    │ Monthly │ Per Task │ Private │ Quality
────────────────────────────┼─────────┼──────────┼─────────┼────────
Cloud: DeepSeek V3          │ €10     │ €0.06    │ ❌      │ Good
Cloud: Claude Sonnet        │ €56     │ €0.35    │ ❌      │ Great
Local on-demand (32B, 4090) │ €15     │ €0.09    │ ✅      │ TBD*
Local always-on (48GB L40S) │ €304    │ €1.90    │ ✅      │ Great
Hybrid (local + cloud 20%)  │ €20     │ €0.13    │ ✅ mostly│ Great
```

*Always flag local model quality as "TBD — benchmark required" until validated.*

## Step 8: Auto-Provisioning Architecture (Hetzner On-Demand)

For on-demand GPU with zero human intervention:

```
State machine:
  COLD → PROVISIONING → LOADING → WARM → REVIEWING → WARM (idle timer)
                                                        ↓ (15 min idle)
                                                    DESTROYING → COLD

Key components:
  - Hetzner API (hcloud CLI or REST) for server lifecycle
  - Persistent Volume for model weights (skip re-download)
  - Snapshot for fast boot (OS + CUDA + vLLM pre-installed)
  - cloud-init script for vLLM startup
  - Health polling (GET /health on vLLM)
  - Cost guard: hard kill at 2h, daily spend cap
```

**Cold start timeline:**
- From snapshot + volume: ~2 min boot + ~1 min vLLM load = ~3 min
- From scratch (first time): ~2 min boot + ~5 min download + ~1 min load = ~8 min

## Can Local GPU Replace Cloud AI Subscriptions?

**Short answer: Not yet as primary model (as of 2026-04).**

Three hard blockers for replacing Claude/GPT as the primary agent model:

1. **Context window** — Claude: 200K tokens. Best local models: 32-128K. Hermes agent sessions routinely hit 50-100K context (skills, memory, conversation history, code).
2. **Tool use / agentic reliability** — Claude's function calling is best-in-class. Local models still fumble multi-step tool chains (wrong params, calling nonexistent tools, poor planning).
3. **Deep reasoning** — Opus-level tasks (chaining vulns into attack narratives, understanding complex auth flows across many subdomains) need frontier reasoning. 32B hallucinates; 235B MoE gets ~70%.

**What local CAN replace today:**
- ✅ Code completion / IDE autocomplete
- ✅ Simple PR reviews (pattern matching)
- ✅ Report formatting and generation
- ✅ Documentation, translation, quick Q&A

**Optimal hybrid strategy:**
- Local on-demand for batch/scanning tasks (~€18/mo)
- Cloud API for agent backbone + deep reasoning (~€50-80/mo)
- Total: €70-100/mo (down from €130-330/mo, 50-70% savings)

**Always-on GPU (€304/mo L40S) only justified when:**
1. Local models close the tool-use gap (check annually)
2. Volume exceeds 500+ tasks/mo (utilization justifies fixed cost)
3. Fine-tuning custom models is a priority
4. GPU is shared across workloads (CV inference, training, etc.)

## Pitfalls

1. **Don't assume cloud prices are stable.** DeepSeek, Google, and OpenAI race to the bottom. Re-check quarterly.
2. **On-demand GPU ≠ always available.** Hetzner GPU servers have limited stock per region. Check availability before committing to on-demand architecture.
3. **VRAM != usable VRAM.** KV cache, CUDA overhead, and vLLM internals consume 2-4GB. A "20GB" GPU realistically fits ~17GB of model weights.
4. **MoE models are deceptive.** Qwen3-235B needs 45GB VRAM loaded but only activates 22B per forward pass — fast output, slow loading.
5. **Benchmark before committing.** Local 32B vs frontier cloud quality is NOT guaranteed. Always run the same test cases through both and compare. The 80% quality threshold is the go/no-go gate.
6. **Dedicated breaks even at ~220h/mo.** If GPU usage exceeds ~30% of the month, switch from on-demand to dedicated.
7. **Volume detach before destroy.** If using Hetzner volumes for model weights, ALWAYS detach the volume before deleting the server. Deleting with volume attached = data loss.
8. **Snapshot the first working setup.** After first successful boot + model load + vLLM running, create a snapshot immediately. This cuts all future cold starts by 5+ minutes.
