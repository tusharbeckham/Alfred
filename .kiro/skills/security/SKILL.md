---
name: security
description: Secure coding, review, and hardening — finding and preventing vulnerabilities, handling secrets, and threat modeling. Use for security reviews or when handling auth, input, or sensitive data.
---

# Security

## Review checklist
- **Injection**: SQL/NoSQL/command/LDAP — use parameterized queries, never string-build.
- **Input validation**: validate/normalize at trust boundaries; reject by default.
- **Output encoding**: context-aware escaping to prevent XSS.
- **AuthN/AuthZ**: verify identity and check permission on every protected action; deny by
  default; no auth logic on the client alone.
- **Secrets**: never in code, logs, or errors. Use env/secret stores. Rotate on exposure.
- **Crypto**: standard libraries only; never roll your own. Strong, current algorithms.
- **Deserialization/SSRF/path traversal**: treat all external input as hostile.
- **Dependencies**: pin versions; watch for known CVEs and typosquatting.

## Handling secrets in this system
- Reference secrets by key name, never print values.
- Flag any `.env`, key, or credential file before it is read or committed.
- Never transmit code/secrets to third-party endpoints unless the Owner asks.

## Threat modeling (lightweight)
1. What are the assets? 2. Who are the actors/attackers? 3. What are the entry points?
4. What can go wrong (STRIDE)? 5. What mitigations exist / are missing?

## Network-exposed services
- If creating an endpoint/server with no auth, SAY SO explicitly and recommend adding
  authentication — never silently ship an open service.

## Reporting
Rank findings by severity (critical/high/med/low) with concrete remediation and file:line.
