# Changelog

Todos los cambios de esta librería. Formato [Keep a Changelog](https://keepachangelog.com/es-ES/1.1.0/),
versiones [SemVer](https://semver.org/lang/es/).

## [Unreleased]

## [0.1.0] - 2026-11-17

### Added

- Cliente de la API `/v1` (versión `2026-10-01`): correos (`send`, `batch`, `list`, `retrieve`), dominios,
  supresiones, plantillas, uso, webhooks, contactos, listas, segmentos, campañas, importaciones y «Conectar
  Mailko» para apps socias.
- `Idempotency-Key` en cada escritura, la misma en cada reintento; reintentos solo cuando es seguro (sin
  respuesta, `409 idempotency_key_in_progress`, `429` con `Retry-After`, `5xx`).
- Un error por estado HTTP (`AuthenticationError`, `PermissionDeniedError`, `RateLimitError`…) con el `code`,
  el `message` en español y el `request_id` de Mailko.
- `build_email` y `check_email`: el cuerpo de un correo y los límites de Mailko revisados antes de llamar.
- `verify_signature` y `construct_event` para los webhooks (`Mailko-Signature`), con varios secretos a la vez.
- Django: `mailko.contrib.django.EmailBackend` (también como mailer de `MAILERS` en Django 6.1), la vista del
  webhook (`mailko.contrib.django.urls`) y las señales `email_event` y `webhook_received`.

### Security

- La llave solo viaja por https (http únicamente hacia un host de desarrollo) y nunca se siguen redirecciones.
