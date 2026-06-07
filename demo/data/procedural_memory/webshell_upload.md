---
id: proc-webshell-upload-md
title: WebShell Upload Network Traceback SOP
alert_type: webshell upload
aliases:
  - webshell
  - malicious file upload
  - web backdoor upload
tags:
  - web
  - upload
  - network traceback
---

# WebShell Upload Network Traceback SOP

## Scope

This SOP is limited to network-level traceback. The workflow should rely on alert logs, audit logs, asset context, and threat intelligence. It should not assume direct host file inspection, memory capture, or endpoint shell artifact collection.

## Investigation Workflow

- Confirm the alert time, target asset, request path, and source IP from the alert stream.
- Pivot on the source IP and target web asset to identify whether similar requests, repeated uploads, or follow-on suspicious access appeared in the same time window.
- Query asset context for the impacted application or host to determine exposed services, owner, environment, and internet-facing status.
- Query threat intelligence on the source IP and any observed domain or URI indicators to assess malicious reputation or known exploitation infrastructure.
- Determine whether the upload alert appears as a single isolated event or as part of broader scanning, exploitation, or follow-up access behavior visible in available logs.

## Logs to Check

- `wazuh-alerts*` for WebShell upload detections, suspicious web activity, or exploit-related alerts
- `security-auditlog*` for application-access audit traces, administrative actions, and relevant access-control records if available
- `wazuh-archives*` if populated later for full raw-event reconstruction around the upload window
- `wazuh-monitoring*` only to verify that telemetry coverage was healthy during the event window

## Asset and Intelligence Queries

- Query asset inventory for the affected web server, application owner, environment, and exposed service profile
- Query whether the same application pattern or path exists on similar assets
- Query source-IP or IOC reputation through threat intelligence services

## Captain Task Tree Initialization

- Create a branch for ingress request reconstruction from `wazuh-alerts*`
- Create a branch for source-IP pivoting across the same asset and neighboring exposed assets
- Create a branch for asset-context review of the affected application and exposure surface
- Create a branch for threat-intelligence enrichment on the source and related indicators

## Decision Focus

- Does the event look like opportunistic scanning, targeted exploitation, or confirmed malicious upload behavior?
- Are there repeated requests or related alerts against the same asset?
- Does the affected asset belong to a broader exposed application family that needs parallel review?
