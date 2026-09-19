<div align="center">
  <h1>AgentTwin Enterprise</h1>
  <p><strong>Enterprise synthetic workforce, digital stakeholders, and AI evaluation — on top of MatrAIx.</strong></p>
  <p>
    This repository is the open-source AgentTwin-8B / MatrAIx + Harbor stack.
    The product direction is an additive enterprise control plane: tenant-scoped
    populations, experiments, policy, and evaluation. It extends population-scale
    persona simulation. It does not replace it.
  </p>
  <p>
    <strong>English</strong> |
    <a href="docs/i18n/README.zh-CN.md">简体中文</a> |
    <a href="docs/i18n/README.zh-TW.md">繁體中文</a> |
    <a href="docs/i18n/README.ko.md">한국어</a> |
    <a href="docs/i18n/README.ja.md">日本語</a> |
    <a href="docs/i18n/README.es.md">Español</a> |
    <a href="docs/i18n/README.pt-BR.md">Português</a>
  </p>
  <p>
    <a href="https://matraix.ai/"><img alt="Website" src="https://img.shields.io/badge/Website-matraix.ai-4f7cff?style=for-the-badge"></a>
    <a href="https://discord.gg/knVyQQnRFa"><img alt="Discord" src="https://img.shields.io/badge/Discord-join%20MatrAIx-5865F2?style=for-the-badge&logo=discord&logoColor=white"></a>
    <a href="https://x.com/MatrAIx2026"><img alt="X" src="https://img.shields.io/badge/X-%40MatrAIx2026-000000?style=for-the-badge&logo=x&logoColor=white"></a>
    <a href="https://www.linkedin.com/company/matraix"><img alt="LinkedIn" src="https://img.shields.io/badge/LinkedIn-MatrAIx-0A66C2?style=for-the-badge&logo=linkedin&logoColor=white"></a>
    <a href="https://forms.gle/hwEHng5HGWRqcJue9"><img alt="Google Form" src="https://img.shields.io/badge/Google%20Form-join%20MatrAIx-4285F4?style=for-the-badge&logo=googleforms&logoColor=white"></a>
    <a href="docs/README.md"><img alt="Docs" src="https://img.shields.io/badge/Docs-Handbook-5b5b5b?style=for-the-badge"></a>
    <a href="https://huggingface.co/datasets/MatrAIx2026/MatrAIx_Persona_1M_Public_Release"><img alt="Hugging Face" src="https://img.shields.io/badge/Hugging%20Face-Persona%201M-ffcc4d?style=for-the-badge"></a>
    <a href="LICENSE"><img alt="License" src="https://img.shields.io/badge/License-MIT-c33b32?style=for-the-badge"></a>
    <a href="docs/quickstart.md#10-playground--play-tasks-visually"><img alt="Playground" src="https://img.shields.io/badge/Playground-Visual%20Runner-56b879?style=for-the-badge"></a>
  </p>
</div>

<div align="center">
  <a href="https://www.youtube.com/watch?v=cNFkz9Wo1y4&t=15s">
    <img src="https://img.youtube.com/vi/cNFkz9Wo1y4/maxresdefault.jpg" alt="Watch the MatrAIx demo on YouTube" width="900">
  </a>
  <p>
    <a href="https://www.youtube.com/watch?v=cNFkz9Wo1y4&t=15s"><img alt="Watch the MatrAIx demo on YouTube" src="https://img.shields.io/badge/%E2%96%B6%20Watch%20the%20demo-on%20YouTube-FF0000?style=for-the-badge&logo=youtube&logoColor=white"></a>
  </p>
</div>

---

**AgentTwin Enterprise** is the control-plane direction for this repository:
tenant-isolated organizations, shaped synthetic workforces, experiment launch
records, policy-gated model routing, and evaluation that never treats
simulated users as human research.

The runnable product underneath is still **MatrAIx** (Python package
`matraix`) plus **Harbor**. Personas remain 1,290-dimension YAML records.
Harbor `Job` / `Trial` still execute Survey, Chatbot, Web, and App tasks.
Playground and `matraix run` are unchanged. Enterprise code lives in
`src/matraix/enterprise/` and is additive.

The name of the simulation layer nods to *The Matrix*: useful for
exploration, stress testing, and hypothesis generation, **not a replacement
for evidence from real people**.

## What works today

The open-source stack is the same research simulator:

- **1,290-dimension personas** with a public [1M coreset](https://huggingface.co/datasets/MatrAIx2026/MatrAIx_Persona_1M_Public_Release)
- Four task environments: **Survey**, **AI Chatbot**, **Web**, **App**
- **Harbor** trials, persona-conditioned agents, task-owned verifiers
- **Playground** visual runner and `uv run matraix run` / `results` / `smoke`
- Shared telemetry and reporting on Harbor job trees (`jobs/`)

See the [Handbook](docs/README.md) and [quickstart](docs/quickstart.md).

## Enterprise status (in progress)

Phases **0–7** are on this branch as a **draft pull request**
([#1](https://github.com/eli-labz/AgentTwin-8B/pull/1)). They are not a
finished production platform. Phases 8–10 (executive reporting, IAM
hardening, packaging) are **not** done.

| Phase | On this branch |
|-------|----------------|
| 0 Domain + tenancy types | Yes — `matraix.enterprise` IDs, entities, in-memory store |
| 1 Persistence + `/api/v1` | Yes — SQLite optional; `matraix enterprise-api` |
| 2 Org graph + population declarations | Yes — 10k-shaped declarations; no `persona/synthesis` rewrite |
| 3 Experiments + cost estimate | Yes — maps to a Harbor **job document**, not `harbor.Job` |
| 4 Model + policy gateways | Yes — beside LiteLLM; `SANDBOX_ONLY` default |
| 5 Runtime planes + local worker | Yes — remote Docker/K8s/queue/batch are **stubs** |
| 6 Telemetry, metrics, evaluation | Yes — OTel-shaped traces; deterministic eval first |
| 7 Enterprise console | Yes — Playground Enterprise mode + `/console`; deep pages stubbed |
| 8–10 Reports, IAM, packaging | Not on this branch |

Python package: `matraix.enterprise`. HTTP: `/api/v1` (OpenAPI at `/docs` when
the enterprise API is running). Default store is in-memory; set
`MATRIX_ENTERPRISE_STORE=sqlite` for a local file.

## Caveats

- **`SANDBOX_ONLY` is the default** enterprise policy. Live / external model
  use requires an explicit tenant policy. `matraix run` defaults are unchanged.
- **Synthetic personas are simulation parameters**, not psychological
  equivalents of employees or customers.
- **Synthetic outputs are not human research.** Evaluation bundles set
  `synthetic_equivalent_to_human_research: false` and recommend human
  validation. Do not present sandbox completions or metrics as usability
  tests, employee consultation, or customer research.
- **No hard-coded secrets.** Optional `MATRIX_ENTERPRISE_API_TOKEN` and model
  keys come from the environment only.
- The core domain stays **cloud-neutral** and **provider-independent**.
  Harbor, Playground, and LiteLLM remain the existing simulation path.

## Quick start

### Install

```bash
git clone https://github.com/eli-labz/AgentTwin-8B.git && cd AgentTwin-8B
uv venv --python 3.12
uv pip install -e .
uv pip install pytest pytest-asyncio httpx
uv pip install -e packages/playground
uv pip install -e packages/harbor-langsmith
uv pip install -e packages/rewardkit
```

Requirements: [uv](https://docs.astral.sh/uv/) and Python 3.12; Docker for
Web and OS-app tasks; Node.js 20+ for Playground / viewer frontends; model
API keys for real persona runs ([agents.md](docs/environment/agents.md)).
Smoke checks do not need a key.

> **Windows users**: run everything inside
> [WSL2](https://learn.microsoft.com/windows/wsl/install). Clone **inside the
> WSL filesystem** (not `/mnt/c/…`) and enable Docker Desktop WSL integration.
> Native PowerShell/cmd is not supported (task verifiers require `bash`).

Run jobs with **`uv run matraix run …`**. Summarize a finished job with
**`uv run matraix results <job>`**. Advanced runtime tools stay under
`uv run harbor …`.

```bash
export ANTHROPIC_API_KEY="sk-ant-..."   # anthropic/claude-* models
# export OPENAI_API_KEY="sk-..."        # openai/gpt-* models
```

Playground can also load keys from `application/playground/.env.local`.

### Smoke tests (no API key)

| Check | Confirms you can run | Command |
|-------|----------------------|---------|
| **Without Docker** | Survey and Chat | `uv run matraix smoke application/tasks/example-survey_product-feedback` |
| **With Docker** | Web and OS-app | `uv run matraix run -c configs/jobs/example-job-recipe/harbor-smoke-local.yaml` |

The first should print `Smoke: ok`. Details:
[quickstart §3](docs/quickstart.md#3-smoke-tests-two-lanes).

### Enterprise API (additive; optional)

Does not change `matraix run`. Default policy remains `SANDBOX_ONLY`.

```bash
# In-memory (process-local)
uv run matraix enterprise-api --port 8090

# Optional durable SQLite + Bearer token (set in the environment; never commit)
export MATRIX_ENTERPRISE_STORE=sqlite
export MATRIX_ENTERPRISE_DB=.enterprise/store.sqlite
# export MATRIX_ENTERPRISE_API_TOKEN=   # when set, send Authorization: Bearer …
uv run matraix enterprise-api --port 8090
```

Open `http://127.0.0.1:8090/docs`. Tenant-scoped routes need `X-Tenant-Id`.
Contract: [docs/enterprise/api.md](docs/enterprise/api.md).

### Import Persona 1M (recommended)

The in-repo `matraix-persona-dev-sample` (~200) is for smoke only.

```bash
huggingface-cli download MatrAIx2026/MatrAIx_Persona_1M_Public_Release \
  --repo-type dataset \
  --local-dir persona/datasets/matraix-persona-1m/release
```

Playground: Dataset → **`matraix-persona-1m`**. CLI:
`--dataset persona/datasets/matraix-persona-1m`.

### GUI and CLI task runs

Playground (two terminals):

```bash
VENV=.venv bash application/playground/backend/run_dev.sh
cd application/playground/frontend && npm ci && npm run dev
```

Open **http://localhost:5173**. Details:
[Playground §10](docs/quickstart.md#10-playground--play-tasks-visually).

CLI — copy a reference task, generate a Harbor job recipe, then run it:

```bash
cp -R application/tasks/example-survey_product-feedback \
  application/tasks/<your-task-name>

uv run python application/scripts/generate_application_job.py \
  --task application/tasks/example-survey_product-feedback \
  --execution-mode auto \
  --persona-ids 0042 \
  --model-name anthropic/claude-sonnet-4-6

uv run matraix run -c configs/jobs/application-task-job-recipe/example-survey-product-feedback-auto-n1.yaml
```

| Type | Reference task |
|------|----------------|
| Survey | `application/tasks/example-survey_product-feedback` |
| Chat | `application/tasks/example-chat-api_support_chatbot` |
| Web | `application/tasks/example-web-playwright_quote-choice` |
| OS-app | `application/tasks/example-computer-use-linux_note-to-csv` |

## Docs

**[Handbook](docs/README.md)** — persona / application / environment guides.

**AgentTwin Enterprise** (this branch):

| Doc | Contents |
|-----|----------|
| [REPOSITORY_AUDIT.md](docs/enterprise/REPOSITORY_AUDIT.md) | What the repo already is |
| [TARGET_ARCHITECTURE.md](docs/enterprise/TARGET_ARCHITECTURE.md) | Layered platform; extend, don’t replace |
| [DOMAIN_MODEL.md](docs/enterprise/DOMAIN_MODEL.md) | Tenant IDs and entities |
| [SECURITY_MODEL.md](docs/enterprise/SECURITY_MODEL.md) | Isolation, policy, secrets |
| [GOVERNANCE.md](docs/enterprise/GOVERNANCE.md) | Synthetic-user rules of use |
| [IMPLEMENTATION_ROADMAP.md](docs/enterprise/IMPLEMENTATION_ROADMAP.md) | Phases 0–10 |
| [api.md](docs/enterprise/api.md) | `/api/v1` |
| [population-builder.md](docs/enterprise/population-builder.md) | 10k-shaped declarations |
| [experiments.md](docs/enterprise/experiments.md) | Launch records → Harbor job YAML |
| [model-gateway.md](docs/enterprise/model-gateway.md) | Policy + routing beside LiteLLM |
| [runtime.md](docs/enterprise/runtime.md) | Control / data / execution planes |
| [telemetry.md](docs/enterprise/telemetry.md) | Traces, metrics, eval SDK |
| [ADR-0001](docs/adr/0001-enterprise-platform-boundaries.md) | Platform boundaries |

<p align="center">
  <img src="docs/assets/matraix-architecture.png" alt="MatrAIx architecture" width="900">
</p>

## Repository layout

```text
AgentTwin-8B/
├── persona/                 Schema, datasets, synthesis/curation/validation
├── application/             Tasks, Playground, generate_application_job.py
├── environment/             Harbor runtime, persona agents, task images
├── packages/                playground · rewardkit · harbor-langsmith
├── apps/viewer/             Frontend paired with `harbor view`
├── configs/jobs/            Curated & generated Harbor job recipes
├── docs/                    Handbook + docs/enterprise/
├── src/matraix/             CLI (`run` / `results` / `smoke` / `enterprise-api`)
│   └── enterprise/          Additive control plane (Phases 0–6)
├── tests/                   Unit / environment / enterprise tests
└── jobs/                    Local Harbor outputs (gitignored)
```

Large generated datasets stay outside git (see the Hugging Face release).

## News & Recognition

- **Academic commentary** — [*Can We Simulate the World?*](https://aiscientist.substack.com/p/can-we-simulate-the-world) — Mayank Kejriwal, [*AI Scientist*](https://aiscientist.substack.com/)
- **Research selection** — Featured on [Hugging Face Papers](https://huggingface.co/papers/2608.04205) ([Daily Papers, 2026-08-10](https://huggingface.co/papers/date/2026-08-10))
- **Media** — [Forbes](https://www.forbes.com/sites/lanceeliot/2026/08/26/using-eight-billion-ai-personas-for-psychology-research-has-its-ups-and-downs/) · [NZZ am Sonntag](https://www.nzz.ch/nzz-am-sonntag/report-und-debatte/die-ki-vermessung-der-menschheit-unsere-acht-milliarden-doppelgaenger-ld.10019342) · [AI Era (via 36Kr)](https://www.36kr.com/p/3932853833759876) · [Numerama](https://www.numerama.com/tech/2308727-ces-chercheurs-ont-cree-83-milliards-dhumains-virtuels-pour-tester-des-produits-a-notre-place.html) · [Infobae](https://www.infobae.com/tecno/2026/08/10/asi-prueba-la-ia-un-mundo-con-8300-millones-de-personas-digitales-matraix-es-el-metaverso/) · [HispanicAd](https://hispanicad.com/news/harvard-and-mit-built-8-3-billion-ai-personas-to-simulate-the-world-report/) · [AI타임스](https://www.aitimes.com/news/articleView.html?idxno=213824) · [Startup Fortune](https://startupfortune.com/harvard-and-mit-built-an-ai-model-of-83-billion-people-to-test-products-on/) · [Forbes Türkiye](https://www.forbes.com.tr/saglik/hastaya-dokunmadan-once-8-3-milyar-kez-denemek-sagligin-yeni-test-dunyasi-matraix) · [WIRED Czech](https://www.wired.cz/news-beat/harvard-a-mit-vytvorily-ai-simulaci-obsahujici-83-miliardy-virtualnich-lidi)
- **Industry commentary** — Discussed by Cisco VP & CTO [Gianpaolo Barozzi](https://lnkd.in/p/gE9cV2nw)
- **Social** — Featured as an [X Trending Story](https://x.com/i/trending/2086626337561911419)

## Releases

- **[2026-08-04]** Technical report on arXiv: [MatrAIx: Simulating the World with 8.3 Billion Persona Agents](https://arxiv.org/abs/2608.04205) (`2608.04205`).
- **[2026-08-01]** Released [Persona 1M](https://huggingface.co/datasets/MatrAIx2026/MatrAIx_Persona_1M_Public_Release) on Hugging Face (~1M quality-filtered personas).
- **[2026-07-31]** Open-sourced the Playground and task library: [MatrAIx-Persona-8B](https://github.com/MatrAIx-ai/MatrAIx-Persona-8B).
- **[2026-07-29]** Position note: [From Personas to Simulated Users](https://matraix.ai/research/survey-from-personas-to-simulated-users.html).

## Join the Community

[![Discord](https://img.shields.io/badge/Discord-join%20MatrAIx-5865F2?style=for-the-badge&logo=discord&logoColor=white)](https://discord.gg/knVyQQnRFa)
[![X](https://img.shields.io/badge/X-follow%20%40MatrAIx2026-000000?style=for-the-badge&logo=x&logoColor=white)](https://x.com/MatrAIx2026)
[![LinkedIn](https://img.shields.io/badge/LinkedIn-follow%20MatrAIx-0A66C2?style=for-the-badge&logo=linkedin&logoColor=white)](https://www.linkedin.com/company/matraix)
[![Google Form](https://img.shields.io/badge/Google%20Form-join%20MatrAIx-4285F4?style=for-the-badge&logo=googleforms&logoColor=white)](https://forms.gle/hwEHng5HGWRqcJue9)

1. Join Discord — nickname **`Full Name - Affiliation`**. Fill the Google Form
   (background, interests, paper authorship / acknowledgements).
2. Say hi to us! We like to connect you for the shared interest or experience!
3. Participating MatrAIx research community for collaboration or contribution!

## Citation

If you use MatrAIx, the Persona 1M dataset, or results from this repository,
please cite:

```bibtex
@article{li2026matraix,
  title         = {MatrAIx: Simulating the World with 8.3 Billion Persona Agents},
  author        = {Li, Xiaomin and Hao, Yuexing and Hou, Jianheng and Huang, Jintao
                   and Wen, Qianfeng and Huang, Shirley and Liu, Yifan and Liu, Xiaoyi
                   and Fan, Yilan and Wang, Yijun and others},
  year          = {2026},
  eprint        = {2608.04205},
  archivePrefix = {arXiv},
  primaryClass  = {cs.AI},
  url           = {https://arxiv.org/abs/2608.04205}
}
```

Paper: [arXiv:2608.04205](https://arxiv.org/abs/2608.04205) ·
Full authors: GitHub **Cite this repository** (`CITATION.cff`) ·
Dataset: [Persona 1M on Hugging Face](https://huggingface.co/datasets/MatrAIx2026/MatrAIx_Persona_1M_Public_Release).

## Star History

<a href="https://www.star-history.com/?repos=MatrAIx-ai%2FMatrAIx-Persona-8B&type=date&legend=top-left">
 <picture>
   <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/chart?repos=MatrAIx-ai/MatrAIx-Persona-8B&type=date&theme=dark&legend=top-left&sealed_token=Yg8UrFwz3ELwyx6wW2CobWIwzUg_VZv53wOwlLji13fApPKuro445vOLW1W5Vy_NfU4NUON-ARepltF9i1-YiNiuSzMK4BVrFHURZLQMoAkeeh4uaxqysfKvTrPQ1cW6zXotcAwoUlKv5Ana5kuWGQj0e-wZoNDaJ6QVL7c8adFut281xX5Quo0c07Y7" />
   <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/chart?repos=MatrAIx-ai/MatrAIx-Persona-8B&type=date&legend=top-left&sealed_token=Yg8UrFwz3ELwyx6wW2CobWIwzUg_VZv53wOwlLji13fApPKuro445vOLW1W5Vy_NfU4NUON-ARepltF9i1-YiNiuSzMK4BVrFHURZLQMoAkeeh4uaxqysfKvTrPQ1cW6zXotcAwoUlKv5Ana5kuWGQj0e-wZoNDaJ6QVL7c8adFut281xX5Quo0c07Y7" />
   <img alt="Star History Chart" src="https://api.star-history.com/chart?repos=MatrAIx-ai/MatrAIx-Persona-8B&type=date&legend=top-left&sealed_token=Yg8UrFwz3ELwyx6wW2CobWIwzUg_VZv53wOwlLji13fApPKuro445vOLW1W5Vy_NfU4NUON-ARepltF9i1-YiNiuSzMK4BVrFHURZLQMoAkeeh4uaxqysfKvTrPQ1cW6zXotcAwoUlKv5Ana5kuWGQj0e-wZoNDaJ6QVL7c8adFut281xX5Quo0c07Y7" />
 </picture>
</a>

## License

MIT — see [LICENSE](LICENSE).
