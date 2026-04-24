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
