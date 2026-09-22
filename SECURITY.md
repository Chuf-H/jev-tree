# Security policy

## Reporting a vulnerability

Please use GitHub's private security-advisory flow instead of opening a public issue for credential leaks,
remote-code execution, unsafe action execution, or server exposure.

## Deployment notes

- Treat `TYPESAFE_API_KEY` as a server-side secret.
- The included demo has no authentication or rate limiting; bind it to localhost by default.
- Add TLS, authentication, request limits, and network controls before internet exposure.
- Put human or policy approval in front of irreversible external actions.
