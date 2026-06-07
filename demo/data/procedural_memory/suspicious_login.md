---
id: proc-suspicious-login-md
title: Suspicious Login Network Traceback SOP
alert_type: suspicious login
aliases:
  - abnormal login
  - unusual login
  - impossible travel login
tags:
  - identity
  - authentication
  - network traceback
---

# Suspicious Login Network Traceback SOP

## Scope

This SOP is limited to network-level traceback. The workflow should rely only on available logs, asset context, and threat intelligence pivots. It should not assume host forensics, memory analysis, or endpoint artifact acquisition.

## Investigation Workflow

- Confirm the alert time window, source IP, target account, target asset, and login result from authentication-related audit logs.
- Pivot on the source IP to determine whether it also attempted access to other internal services, identities, or exposed systems during the same period.
- Query asset context for the target system and account to understand business role, exposure level, ownership, and whether the access path is expected.
- Query threat intelligence for the source IP to determine whether it is associated with brute-force infrastructure, anonymization services, scanners, or known malicious reputation.
- Correlate the suspicious login with other network-visible security alerts to determine whether the activity is isolated or part of a larger campaign.

## Logs to Check

- `security-auditlog*` for failed logins, successful logins, access-control decisions, and operator activity
- `wazuh-alerts*` for related brute-force alerts, suspicious access patterns, and correlated detections
- `wazuh-archives*` if available later for deeper raw-event reconstruction across the same time window
- `wazuh-monitoring*` only to confirm whether logging blind spots may exist because of agent or collector health issues

## Asset and Intelligence Queries

- Query internal asset inventory for the target host, exposed services, owner, and business criticality
- Query account ownership or role mapping for the impacted identity
- Query external or internal threat intelligence for the source IP reputation, tags, and historical observations

## Captain Task Tree Initialization

- Create a branch for authentication timeline reconstruction from `security-auditlog*`
- Create a branch for source-IP pivoting across `wazuh-alerts*` and any available audit events
- Create a branch for target-asset context review using asset inventory and account ownership data
- Create a branch for source-IP threat-intelligence enrichment and confidence scoring

## Decision Focus

- Was the login successful, or was it only a failed attempt?
- Did the same source IP touch multiple accounts, services, or assets?
- Does asset context or threat intelligence increase confidence that the event is malicious?
