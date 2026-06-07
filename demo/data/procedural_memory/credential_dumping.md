---
id: proc-credential-dumping-md
title: Credential Dumping Network Traceback SOP
alert_type: credential dumping
aliases:
  - lsass dump
  - mimikatz activity
  - credential extraction
tags:
  - credentials
  - privilege abuse
  - network traceback
---

# Credential Dumping Network Traceback SOP

## Scope

This SOP is limited to network-level traceback. The workflow should rely on alert logs, audit logs, vulnerability state, asset context, and threat intelligence. It should not assume direct LSASS memory inspection, host triage, or offline forensic acquisition.

## Investigation Workflow

- Confirm the alert time, source asset, target account context, and related detection metadata from the security alert stream.
- Pivot on the impacted host, account, or user context to identify whether nearby authentication anomalies or privilege-related alerts exist in the same timeline.
- Query asset context to determine whether the impacted system is high value, privileged, internet-facing, or tied to administrative workflows.
- Query vulnerability and exposure data to determine whether the host already carries exploitable weaknesses or high-risk software posture that may explain follow-on credential abuse.
- Query threat intelligence for any external IPs, domains, or related indicators that appear near the event timeline to determine whether the host is part of a broader intrusion pattern.

## Logs to Check

- `wazuh-alerts*` for credential-access alerts, suspicious process-behavior detections, and privilege-related alerts
- `security-auditlog*` for related login activity, access decisions, and operator actions around the same window
- `wazuh-states-vulnerabilities*` for known CVEs, affected versions, and risk context on the asset
- `wazuh-archives*` if populated later for raw-event reconstruction

## Asset and Intelligence Queries

- Query internal asset inventory for host role, owner, exposure, and administrative importance
- Query account-role mapping for privileged or service-account impact
- Query threat intelligence for surrounding external indicators that may connect this event to known attacker infrastructure

## Captain Task Tree Initialization

- Create a branch for alert-timeline reconstruction across `wazuh-alerts*`
- Create a branch for authentication and privilege-context review from `security-auditlog*`
- Create a branch for asset criticality and vulnerability-surface review using `wazuh-states-vulnerabilities*`
- Create a branch for external-indicator enrichment and campaign correlation

## Decision Focus

- Is this event isolated, or does it align with suspicious login or lateral-movement evidence?
- Does the impacted system have high-value credential exposure risk?
- Do vulnerability state and intelligence pivots suggest a broader compromise path?
