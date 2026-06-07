---
id: proc-sql-injection-md
title: SQL Injection Network Traceback SOP
alert_type: sql injection
aliases:
  - sqli
  - injection payload
tags:
  - web
  - database
  - network traceback
---

# SQL Injection Network Traceback SOP

## Scope

This SOP is limited to network-level traceback. The workflow should rely on available alert logs, audit logs, asset context, threat intelligence, and vulnerability state. It should not assume direct database host forensics or application code debugging.

## Investigation Workflow

- Confirm the alert time, request path, payload characteristics, source IP, and target asset from the alert stream.
- Pivot on the same source IP, URI path, or application asset to identify repeated probing, broader scanning, or follow-on exploitation attempts.
- Query asset context for the application owner, exposed interfaces, criticality, and database dependency.
- Query threat intelligence for the source IP to determine whether it is linked to scanning, exploit automation, or malicious infrastructure.
- Query vulnerability state and related exposure records to understand whether the asset already has known weakness context that increases the credibility of the alert.

## Logs to Check

- `wazuh-alerts*` for SQL injection detections, web exploitation alerts, and related attack chaining
- `security-auditlog*` for access-control decisions, administrative actions, or supporting application audit traces if present
- `wazuh-states-vulnerabilities*` for known CVEs or vulnerable software versions related to the affected application stack
- `wazuh-archives*` if populated later for broader raw-event reconstruction

## Asset and Intelligence Queries

- Query internal asset inventory for application ownership, exposed service map, and business criticality
- Query whether similar applications or paths exist on peer assets
- Query threat intelligence for source-IP reputation and exploit-infrastructure associations

## Captain Task Tree Initialization

- Create a branch for request-path and payload reconstruction from `wazuh-alerts*`
- Create a branch for source-IP pivoting and same-asset repeated-attempt analysis
- Create a branch for affected-application context review using asset inventory
- Create a branch for vulnerability-surface and threat-intelligence enrichment

## Decision Focus

- Does the alert reflect casual scanning or credible exploitation behavior?
- Are there repeated probes against the same application or adjacent services?
- Does asset exposure or known vulnerability state raise confidence that the event is actionable?
