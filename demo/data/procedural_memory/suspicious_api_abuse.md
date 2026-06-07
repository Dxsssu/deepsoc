---
id: proc-suspicious-api-abuse-md
title: Suspicious API Abuse Network Traceback SOP
alert_type: suspicious api abuse
aliases:
  - api abuse
  - anomalous api access
  - token abuse
tags:
  - api
  - application
  - network traceback
---

# Suspicious API Abuse Network Traceback SOP

## Scope

This SOP is limited to network-level traceback. The workflow should rely on alert logs, audit logs, asset context, and threat intelligence. It should not assume direct source-code review or host-side process analysis.

## Investigation Workflow

- Confirm the API endpoint, source IP, credential or token context, request rate, and alert time window.
- Pivot on the same token, account, source IP, or endpoint pattern to identify repeated misuse across services or assets.
- Query asset context for the API service to determine owner, exposure, business criticality, and expected consumer profile.
- Query threat intelligence on the source IP and associated infrastructure if the access originates externally or through unusual paths.
- Correlate the API abuse with suspicious login, privilege escalation, or data exfiltration alerts to determine whether the misuse is part of a broader attack sequence.

## Logs to Check

- `wazuh-alerts*` for API anomaly alerts, abuse detections, and correlated access anomalies
- `security-auditlog*` for token use, access-control decisions, and related identity actions
- `wazuh-archives*` if populated later for detailed raw-event reconstruction around the endpoint and actor

## Asset and Intelligence Queries

- Query API service ownership, exposure, and expected client behavior
- Query account or token ownership and baseline usage profile
- Query external source-IP reputation when applicable

## Captain Task Tree Initialization

- Create a branch for API request-pattern reconstruction
- Create a branch for token/account and source-IP pivoting
- Create a branch for service context and expected-consumer validation
- Create a branch for downstream impact correlation such as exfiltration or privilege misuse

## Decision Focus

- Is the API pattern expected for the account or client type?
- Does the same actor abuse multiple endpoints or services?
- Does the event connect to broader compromise or data-access risk?
