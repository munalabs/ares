# Ares — Sessions

---

## 2026-04-24 — Integración al ecosistema Muna

**Contexto:** Sesión de diseño del ecosistema. Ares fue definido como el
worker de pentest dinámico — corre remoto, consume jobs de Argos vía NATS,
y persiste conocimiento en la Knowledge Base de Argos.

**Decisiones tomadas:**
- NATS consumer para `jobs.dynamic.pending` y `jobs.mobile.pending`
- HTTP trigger endpoint como fallback/desarrollo (`POST /engage`)
- Consulta de Knowledge Base de Argos al inicio de cada job (no redescubrir lo conocido)
- Mobile (APK/IPA) como target de primera clase — descarga desde URL firmada de Argos
- Diff-aware: dado `changed_endpoints` en `DiffContext`, focalizar fases OWASP WSTG
- Aislamiento de red por engagement (Docker network por job)
- Límite de engagements paralelos configurable por plan de tenant
- Reporte JSON existente se mapea a `JobResult` de muna-agentsdk

**Estado actual del repo:**
- Hermes agent funcional con 13 fases OWASP WSTG
- 46 sub-skills de seguridad implementados
- MCP tools: nmap, nuclei, sqlmap, ffuf, dalfox, MoBSF, Frida, ZAP, etc.
- Reporte JSON de hallazgos funcional
- 0 tests unitarios
- Sin integración NATS
- Sin integración con Knowledge Base de Argos
- Sin HTTP trigger endpoint

**Próximos pasos (por dónde arrancar):**
1. Épica 5 (muna-agentsdk) — adoptar contratos del ecosistema
2. Épica 1 (NATS consumer) — conectarse al ecosistema
3. Épica 2 (HTTP trigger) — fallback para desarrollo
4. Épica 3 (Knowledge Base) — consulta de superficie anterior
5. Épica 8 (tests) — deuda crítica

**Preguntas abiertas:**
- Ninguna al cierre de esta sesión
