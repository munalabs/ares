# Ares — Context

## Qué es

Ares es el motor de pentest dinámico del ecosistema Muna. Analiza aplicaciones
en runtime — web, Android (APK), e iOS (IPA) — con cobertura OWASP WSTG
completa. Solo reporta vulnerabilidades con PoC funcionando. Consulta la
Knowledge Base de Argos antes de correr para enfocar el análisis en lo nuevo.

## Rol en el ecosistema

- Consume jobs de `jobs.dynamic.pending` y `jobs.mobile.pending` en NATS
- Consulta superficie anterior desde Argos Knowledge Base (endpoints, auth contexts, findings previos)
- Corre engagement focalizado en lo que cambió (diff-aware)
- Escribe superficie actualizada y findings a Argos Knowledge Base
- Publica resultado JSON a `jobs.results`

## Lo que NO hace

- No analiza código fuente estáticamente (eso es Auspex)
- No persiste estado entre engagements localmente (todo a Argos)
- No se invoca directamente — siempre a través de Argos vía NATS

## Decisiones de arquitectura relevantes

- **Consulta de Knowledge Base primero**: antes de correr, Ares pregunta a Argos qué sabe del target. No redescubre lo que ya mapeó en engagements anteriores.
- **Findings validados únicamente**: cada vulnerabilidad requiere un PoC funcionando antes de entrar al reporte. No hay findings especulativos.
- **Aislamiento de red por engagement**: cada contenedor de engagement corre en su propia red Docker — sin cross-contamination entre clientes.
- **Diff-aware para web**: dado `DiffContext.changed_endpoints`, Ares prioriza las fases OWASP WSTG relevantes para los endpoints cambiados.
- **Mobile como target de primera clase**: APK/IPA se descargan desde URL firmada (Argos artifact store). Pipeline: MoBSF → Frida → ADB.

## Dependencias

- `muna-agentsdk` — contratos de JobSpec, JobResult, Finding
- `muna-vaultsdk` — resolver credenciales de targets
- `muna-authsdk` — validar tokens de Argos
- `muna-telemetry` — eventos de telemetría
- Argos Knowledge Base API — leer superficie anterior, escribir hallazgos
- NATS/JetStream — consumir jobs, publicar resultados
- Hermes Agent — orquestador interno de Ares
- MCP tools — nmap, nuclei, sqlmap, ffuf, dalfox, MoBSF, Frida, ZAP, etc.

## Estado actual (2026-04-30)

**En producción en muna1.** 58 tests + 1 xfail documentado.

Stack deployado en muna1:
- `ares-hermes` — Hermes v0.11.0, LLM: claude-sonnet-4-6 vía OpenRouter, MCP tools: playwright, pentest-ai, gitnexus, mobsf (parcial), apk-sast, adb, frida (parcial)
- `ares-adapter` — NATS consumer subscrito a `jobs.dynamic.pending` + `jobs.mobile.pending`

Cadena de invocación para modo desatendido:
```
Argos → NATS jobs.dynamic.pending → ares-adapter
      → docker exec ares-hermes hermes chat --yolo -q @/workspace/<brief>
      → findings → NATS jobs.results → Argos
```

La integración de Ares con el ecosistema Muna está operacional:
- NATS/JetStream consumer: `jobs.dynamic.pending` + `jobs.mobile.pending` con pull_subscribe, max_deliver=3, backoff
- HTTP adapter (`ares_integration/`): POST /engage, GET status/result con X-Tenant-Id
- Knowledge Base: fetch surface (brief enrichment) + push surface (post-engagement)
- Scope firewall: IPs privadas, loopback, 169.254.x.x, IPv4-mapped IPv6 bloqueados
- Injection resistance: `_INJECTION_RESISTANCE_BRIEF` + `_TOOL_RATE_LIMITS` antes de "Go."
- Diferential scope: DIFFERENTIAL SCOPE section en brief con changed_endpoints/changed_files
- Mobile pipeline: MoBSF static (siempre) + ADB/Frida dynamic (`ARES_DYNAMIC_ANALYSIS=true`)
- CF Tunnel TCP: adb.munalabs.eu:5038 y frida.munalabs.eu:27042 (hermes-ai → muna1)
- Concurrency limit: `ARES_MAX_PARALLEL_ENGAGEMENTS=3`, TTL eviction de jobs completados
- Seccomp + no-new-privileges en docker-compose.yml

### Problemas conocidos (2026-04-30)

- **KB 302**: el adapter accede a Argos vía `argos.munalabs.eu` (CF Access), que redirige con 302. Debe usar `http://argos:8000` (red muna-argos). La fetch de superficie falla, por eso el brief no incluye contexto previo.
- **MoBSF no arranca**: el compose de `ares-hermes` depende de MoBSF con healthcheck, pero MoBSF falla con `MOBSF_HOME=None`. MoBSF se omitió en el start inicial.
- **Consumer single-threaded**: el NATS consumer procesa un job a la vez. El segundo job queda en cola hasta que el primero completa o timeout.
- **ares-adapter user: "0"**: en rootless Docker, el container debe correr como uid 0 (= host uid 1000) para acceder al socket `/run/user/1000/docker.sock`. Contra-intuitivo pero correcto.
- **auth_context requerido en DynamicTarget**: la validación actual requiere un VaultRef UUID válido incluso para targets sin auth. Fix pendiente en muna-agentsdk.

## Variables de configuración

```
NATS_URL=nats://muna-nats:4222
ARES_ENGAGE_URL=http://localhost:8001
ARGOS_URL=https://argos.munalabs.eu      # TODO: cambiar a http://argos:8000
ARGOS_TOKEN=<service token non-expiring>
ARES_TRIGGER=docker                      # docker | subprocess | ssh
ARES_HERMES_CONTAINER=ares-hermes
ARES_SHARED_WORKSPACE=/workspace
HERMES_PROFILE=pentest
ARES_MAX_PARALLEL_ENGAGEMENTS=3
ARES_WORKER_ID=ares-prod-1
# Mobile (opcional)
MOBSF_URL=http://localhost:8100
MOBSF_API_KEY=<key>
ANDROID_ADB_SERVER_HOST=127.0.0.1
ANDROID_ADB_SERVER_PORT=5038
FRIDA_TCP_HOST=127.0.0.1
FRIDA_TCP_PORT=27042
ARES_DYNAMIC_ANALYSIS=true
```

## Próximos pasos

- Fix KB fetch: usar `http://argos:8000` desde ares-adapter (evita CF Access 302)
- Fix MoBSF startup: `MOBSF_HOME` no seteado en compose env
- auth_context opcional en DynamicTarget (muna-agentsdk)
- Aislamiento de red por engagement (Docker network per job)
- Consumer concurrente (asyncio.gather para múltiples jobs simultáneos)
