---
id: proc-web-directory-scanning-md
title: Web Directory Scanning Network Traceback SOP
alert_type: web directory scanning
aliases:
  - web scan
  - content discovery
  - directory brute force
tags:
  - web
  - reconnaissance
  - network traceback
---

# Web Directory Scanning Network Traceback SOP

## Scope

This SOP is limited to network-level traceback. The workflow should rely on alert logs, audit logs, asset context, and threat intelligence. It should not assume web-server local file verification.

## Investigation Workflow

- Confirm the scan time window, source IP, target asset, requested path patterns, response-code profile, and scan rate.
- Pivot on the source IP and path pattern to determine whether multiple applications or hosts were scanned in the same period.
- Query asset context for the targeted web application to determine exposure, criticality, owner, and whether the scanned paths map to sensitive functionality.
- Query threat intelligence for the source IP and any scanner signatures to assess whether the activity maps to known reconnaissance or exploit tooling.
- Correlate the directory scan with later exploit attempts, file-upload alerts, or suspicious logins to determine whether reconnaissance progressed.

## Logs to Check

- `wazuh-alerts*` for web reconnaissance detections, scanning signatures, and follow-on exploit alerts
- `security-auditlog*` for supporting application audit traces or access-control decisions if available
- `wazuh-archives*` if populated later for broader raw HTTP or audit-event reconstruction

## Asset and Intelligence Queries

- Query targeted application ownership, exposure, and business criticality
- Query whether peer applications expose similar paths or technologies
- Query source-IP reputation and scanner-tool associations

## Captain Task Tree Initialization

- Create a branch for path-pattern and response-profile reconstruction
- Create a branch for source-IP pivoting across applications and hosts
- Create a branch for target-application context review
- Create a branch for progression analysis into exploit or login activity

## Decision Focus

- Is the activity broad reconnaissance or targeted path discovery?
- Are the scanned paths aligned with sensitive functionality?
- Did the same source progress into exploitation or credential abuse?
