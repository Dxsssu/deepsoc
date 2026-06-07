---
id: proc-port-scan-md
title: Port Scan Network Traceback SOP
alert_type: port scan
aliases:
  - network scan
  - service probing
  - recon scan
tags:
  - reconnaissance
  - scanning
  - network traceback
---

# Port Scan Network Traceback SOP

## Scope

This SOP is limited to network-level traceback. The workflow should rely on network-visible alert data, asset context, and threat intelligence. It should not assume host-level confirmation of every probed service.

## Investigation Workflow

- Confirm the source IP, destination scope, targeted ports, scan rate, and time window.
- Pivot on the source IP to determine whether it scanned additional assets, subnets, or service groups beyond the initial alert.
- Query asset context for the scanned assets to identify business criticality, internet exposure, and whether the observed ports map to sensitive services.
- Query threat intelligence on the source IP to assess whether it is associated with mass scanning, botnet activity, or known adversary reconnaissance infrastructure.
- Correlate the scan with later exploitation or authentication alerts to determine whether the activity progressed beyond reconnaissance.

## Logs to Check

- `wazuh-alerts*` for scan detections, recon alerts, and related exploit or brute-force events
- `security-auditlog*` for any access attempts or related administrative actions near the same window
- `wazuh-archives*` if populated later for raw-event detail across the same source and target set
- `wazuh-monitoring*` only to validate telemetry health

## Asset and Intelligence Queries

- Query asset inventory for the scanned hosts, exposed services, and criticality
- Query whether the targeted ports correspond to sensitive or externally accessible services
- Query source-IP threat intelligence and known scanning reputation

## Captain Task Tree Initialization

- Create a branch for scan-scope reconstruction from `wazuh-alerts*`
- Create a branch for source-IP pivoting across assets, ports, and time windows
- Create a branch for scanned-asset exposure and criticality review
- Create a branch for post-scan progression analysis into exploitation or authentication events

## Decision Focus

- Was this broad internet noise or targeted reconnaissance?
- Are the scanned ports tied to high-value or sensitive services?
- Did the same source progress from scanning into access attempts or exploitation?
