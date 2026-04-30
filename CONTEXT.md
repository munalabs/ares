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

## Estado actual (2026-04-28)

**En producción.** 58 tests + 1 xfail documentado.

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

## Variables de configuración

```
NATS_URL=nats://localhost:4222
ARES_ENGAGE_URL=http://localhost:8001
ARGOS_URL=http://localhost:8000
ARGOS_TOKEN=<service token>
ARES_MAX_PARALLEL_ENGAGEMENTS=3
# Mobile (opcional)
MOBSF_URL=http://localhost:8100
MOBSF_API_KEY=<key>
ANDROID_ADB_SERVER_HOST=127.0.0.1      # via CF Tunnel
ANDROID_ADB_SERVER_PORT=5038
FRIDA_TCP_HOST=127.0.0.1
FRIDA_TCP_PORT=27042
ARES_DYNAMIC_ANALYSIS=true
```

## Próximos pasos

- Aislamiento de red por engagement (Docker network por job)
- Keycloak auth en el endpoint /engage (hoy sin auth, red interna como boundary)
- Modo diff para mobile (dos versiones de APK)
