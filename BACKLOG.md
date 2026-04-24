# ares — Backlog

Motor de pentest dinámico. Corre remoto, consume jobs de Argos vía NATS,
analiza aplicaciones en runtime, devuelve findings con PoCs y reporte JSON.
Soporta tres tipos de target: web (URL), Android (APK), iOS (IPA).

Prerequisito: muna-agentsdk.

---

## Épica 1 — Integración con NATS / Argos

- [ ] Investigar Hermes webhooks (feature reciente) como mecanismo de trigger interno
  - Si Hermes expone un webhook endpoint, el NATS consumer puede ser una capa fina que llama a Hermes vía HTTP al recibir un job
  - Explorar antes de implementar el consumer — puede simplificar la integración significativamente
- [ ] Implementar consumer NATS para `jobs.dynamic.pending` y `jobs.mobile.pending`
  - Deserializar `JobSpec` de muna-agentsdk
  - Construir brief de engagement a partir del target y `DiffContext`
  - Trigger a Hermes (vía webhook o invocación directa según investigación)
- [ ] Publicar resultados a `jobs.results` al completar
  - Mapear reporte JSON existente al formato `JobResult` de muna-agentsdk
- [ ] Manejo de errores: publicar `status: failed` con razón si el engagement falla
- [ ] Heartbeat — señalizar a Argos que el worker está vivo
  - Frecuencia: cada 30s
  - Publicar en `jobs.heartbeat` con `{job_id, worker_id, timestamp}`
  - Argos marca el job como `failed` si no recibe heartbeat en 60s

---

## Épica 2 — HTTP trigger endpoint

Para casos donde Argos quiere triggerear Ares directamente sin NATS
(fallback o modo desarrollo).

- [ ] `POST /engage` — recibe `JobSpec`, inicia engagement, devuelve `job_id`
- [ ] `GET /engage/{job_id}/status` — estado del engagement en curso
- [ ] `GET /engage/{job_id}/result` — resultado cuando completo
- [ ] Autenticación vía muna-authsdk

---

## Épica 3 — Consulta de Knowledge Base

Antes de correr un engagement, Ares consulta Argos para no redescubrir lo
que ya sabe.

- [ ] Al inicio del job, fetch de `GET /api/knowledge/{target_id}/surface`
  - Endpoints ya descubiertos, auth contexts que funcionaron, findings anteriores
- [ ] Usar esa información para enfocar el engagement en lo nuevo
- [ ] Al terminar, escribir superficie actualizada y findings a Argos

---

## Épica 4 — Análisis diferencial (diff-aware)

Dado que un deploy puede cambiar solo algunos endpoints o flujos, Ares debe
poder enfocar el pentest en lo que cambió.

- [ ] Consumir `DiffContext.changed_endpoints` del `JobSpec`
- [ ] Priorizar fases del OWASP WSTG relevantes para los endpoints cambiados
- [ ] Modo full scan para primer engagement o cuando no hay diff context
- [ ] Razonamiento interno: dado el diff, construir scope de testing focalizado

---

## Épica 5 — Adoptar SDKs del ecosistema

- [ ] Mapear findings de Ares al tipo `Finding` de muna-agentsdk
- [ ] Adoptar muna-authsdk + Keycloak
  - Registrar client `ares` en muna-keycloak con `client_credentials`
- [ ] Adoptar muna-vaultsdk — resolver credenciales de targets desde Vault
- [ ] Adoptar muna-telemetry — emitir eventos hacia muna-sentinel
  - `job_started`, `job_completed`, `job_failed`, `finding_emitted`

---

## Épica 6 — Targets móviles (APK / IPA)

Ares ya tiene soporte parcial para mobile (MoBSF, Frida, ADB). Hay que
formalizarlo como target type de primera clase en el ecosistema.

`MobileTarget` es un tipo separado en muna-agentsdk (no extensión de
DynamicTarget), con `analysis_type: "mobile"` y subject NATS `jobs.mobile.pending`.

- [ ] Consumir `MobileTarget` del `JobSpec` (`analysis_type: "mobile"`)
  - `artifact_url` es una URL firmada con TTL provista por Argos
  - Descargar el APK/IPA al inicio del engagement, eliminarlo al terminar
- [ ] Rutear internamente a MoBSF + Frida pipeline según `platform`
- [ ] Knowledge base: persistir superficie descubierta por versión de APK/IPA
- [ ] Modo diff para mobile: dado dos versiones del APK, enfocar en lo que cambió

---

## Épica 7 — Scope enforcement en runtime

Durante un engagement, Ares puede descubrir URLs, subdominios, o endpoints
fuera del scope declarado. Sin enforcement, Ares los atacaría — lo cual puede
ser ilegal si están fuera del scope del cliente.

- [ ] Implementar scope firewall: toda URL que Ares intente atacar se valida contra `DynamicTarget.scope`
- [ ] Si la URL está fuera de scope: loguear el hallazgo como "discovered out-of-scope" pero NO atacar
- [ ] Incluir endpoints out-of-scope en el reporte como hallazgos informativos (no findings de seguridad)
- [ ] El scope es inmutable durante el engagement — no puede ser modificado por el agente en runtime

---

## Épica 8 — Concurrencia y aislamiento

Ares puede correr múltiples engagements en paralelo pero cada uno debe estar
completamente aislado — red, filesystem, y recursos.

- [ ] Definir límite de engagements paralelos (configurable, default por plan de tenant)
- [ ] Aislamiento de red por engagement — cada contenedor en su propia red Docker
- [ ] Límites de CPU y memoria por contenedor de engagement
- [ ] Queue interna si se supera el límite — jobs esperan sin perderse
- [ ] Timeout por engagement configurable (default: 4h) — si no termina, marcar como `failed` y notificar a Argos
  - Debe ser mayor al timeout de job configurado en Argos para el mismo job

---

## Épica 9 — Testing

Hoy Ares tiene 0 tests unitarios. Mínimo viable para un sistema de seguridad:

- [ ] Tests del consumer NATS (mock de JetStream)
- [ ] Tests del mapeo JobSpec → brief de engagement (web, APK, IPA)
- [ ] Tests del mapeo reporte JSON → JobResult
- [ ] Tests del endpoint HTTP trigger
