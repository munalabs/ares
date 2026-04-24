# Ares — Rules

## Seguridad

- **Scope estricto.** Ares solo puede atacar el target explicitamente definido en el `JobSpec`. Cualquier descubrimiento fuera del scope declarado se ignora — no se persigue aunque sea obvio.
- **Nunca persistir credenciales de targets.** Auth contexts que funcionaron van a Argos Knowledge Base vía API. Si necesitás guardar algo localmente durante el engagement, en memoria o en un tmpfs del contenedor — nunca en disco persistente.
- **Aislamiento de red obligatorio.** Cada engagement corre en su propia red Docker. Un engagement no puede ver el tráfico de otro.

## Arquitectura

- **Solo findings con PoC.** Si una vulnerabilidad no tiene un PoC funcionando, no va al `JobResult`. Es preferible reportar menos con certeza que reportar más con especulación.
- **Knowledge Base primero.** Siempre consultar Argos antes de iniciar el engagement. Empezar desde cero cuando Argos ya sabe cosas del target es un bug, no un comportamiento aceptable.
- **El reporte JSON es el contrato de salida.** El mapeo a `JobResult` de muna-agentsdk se hace desde el reporte JSON existente. No cambiar el formato del reporte JSON sin actualizar el mapper.

## Código

- **Cero tests es el estado de deuda, no el estado objetivo.** Todo nuevo código que se agregue debe tener tests. La meta es llegar a cobertura equivalente a Auspex.
- **Los adapters de NATS y HTTP son el punto de integración.** El core de Ares (Hermes + skills) no sabe que existe NATS. El adapter traduce.
