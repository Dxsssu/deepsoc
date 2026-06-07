---
id: proc-lateral-movement-md
title: Lateral Movement Network Traceback SOP
alert_type: lateral movement
aliases:
  - remote execution
  - internal propagation
  - east-west movement
tags:
  - lateral movement
  - internal network
  - network traceback
---

# Lateral Movement Network Traceback SOP

## Scope

This SOP is limited to network-level traceback. The workflow should rely on authentication logs, alert streams, asset context, and available vulnerability or exposure information. It should not assume host-based forensic validation of process execution.

## Investigation Workflow

- Confirm the source host, target host, target service, time window, and whether the movement signal is based on authentication, remote service use, or correlated alert behavior.
- Reconstruct the east-west path by pivoting across the same source host, target host, and account identifiers within the same time window.
- Query asset context for both source and target systems to determine criticality, ownership, role, and whether communication between them is expected.
- Query vulnerability state to determine whether the target system presents known exposure or weakness patterns that align with the movement path.
- Correlate with other alerts such as suspicious login, credential dumping, or privilege-related detections to determine whether the movement is part of a larger attack chain.

## Logs to Check

- `security-auditlog*` for authentication events, remote access records, and access-control actions
- `wazuh-alerts*` for lateral-movement detections, remote-execution alerts, and suspicious credential-use patterns
- `wazuh-states-vulnerabilities*` for target-host weakness or exposure context
- `wazuh-archives*` if populated later for deeper raw-event reconstruction

## Asset and Intelligence Queries

- Query internal asset inventory for source and target host role, owner, environment, and trust zone
- Query account-role mapping for accounts seen crossing hosts
- Query external intelligence only if associated external indicators appear nearby in the same campaign timeline

## Captain Task Tree Initialization

- Create a branch for east-west authentication timeline reconstruction
- Create a branch for source-target asset relationship and trust-zone review
- Create a branch for account-context review across the movement path
- Create a branch for vulnerability and exposure review on the target host

## Decision Focus

- Is the communication path expected in business operations?
- Does the same account or source host appear across multiple targets?
- Does the broader alert context support escalation to coordinated intrusion response?
