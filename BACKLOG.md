# ares — Backlog

Motor de pentest dinámico. Corre remoto, consume jobs de Argos vía NATS,
analiza aplicaciones en runtime, devuelve findings con PoCs y reporte JSON.
Soporta tres tipos de target: web (URL), Android (APK), iOS (IPA).

Prerequisito: muna-agentsdk.

---

## Épica 1 — Integración con NATS / Argos

- [ ] Implementar consumer NATS para `jobs.dynamic.pending`
  - Deserializar `JobSpec` de muna-agentsdk
  - Construir brief de engagement a partir del `DynamicTarget` y `DiffContext`
- [ ] Publicar resultados a `jobs.results` al completar
  - Mapear reporte JSON existente al formato `JobResult` de muna-agentsdk
- [ ] Manejo de errores: publicar `status: failed` con razón si el engagement falla
- [ ] Heartbeat — señalizar a Argos que el worker está vivo

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

## Épica 5 — Adoptar muna-agentsdk

- [ ] Mapear findings de Ares al tipo `Finding` de muna-agentsdk
- [ ] Adoptar muna-authsdk
- [ ] Adoptar muna-telemetry — emitir eventos estándar del ecosistema

---

## Épica 6 — Targets móviles (APK / IPA)

Ares ya tiene soporte parcial para mobile (MoBSF, Frida, ADB). Hay que
formalizarlo como target type de primera clase en el ecosistema.

- [ ] Extender `DynamicTarget` en muna-agentsdk para soportar:
  - `MobileTarget`: `platform: Literal["android", "ios"]`, `artifact_url: str`
  - `artifact_url` apunta a un APK o IPA almacenado (S3, GCS, o URL firmada)
- [ ] Lancer: tool `analyze_mobile` — sube el artefacto, construye `JobSpec` con `MobileTarget`
- [ ] Argos: manejar upload y storage temporal del artefacto (URL firmada con TTL)
- [ ] Ares: consumir `MobileTarget`, rutear internamente a MoBSF + Frida pipeline
- [ ] Knowledge base: persistir superficie descubierta por versión de APK/IPA
- [ ] Modo diff para mobile: dado dos versiones del APK, enfocar en lo que cambió

---

## Épica 7 — Concurrencia y aislamiento

Ares puede correr múltiples engagements en paralelo pero cada uno debe estar
completamente aislado — red, filesystem, y recursos.

- [ ] Definir límite de engagements paralelos (configurable, default por plan de tenant)
- [ ] Aislamiento de red por engagement — cada contenedor en su propia red Docker
- [ ] Límites de CPU y memoria por contenedor de engagement
- [ ] Queue interna si se supera el límite — jobs esperan sin perderse
- [ ] Timeout por engagement — si no termina en N horas, marcar como `failed`

---

## Épica 8 — Testing

Hoy Ares tiene 0 tests unitarios. Mínimo viable para un sistema de seguridad:

- [ ] Tests del consumer NATS (mock de JetStream)
- [ ] Tests del mapeo JobSpec → brief de engagement (web, APK, IPA)
- [ ] Tests del mapeo reporte JSON → JobResult
- [ ] Tests del endpoint HTTP trigger
