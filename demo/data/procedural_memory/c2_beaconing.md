---
id: proc-c2-beaconing-md
title: C2 Beaconing Network Traceback SOP
alert_type: c2 beaconing
aliases:
  - beaconing
  - malware beacon
  - command and control
tags:
  - c2
  - beaconing
  - network traceback
---

# C2 Beaconing Network Traceback SOP

## Scope

This SOP is limited to network-level traceback. The workflow should rely on alert logs, asset context, audit logs, and threat intelligence. It should not assume malware reverse engineering or host-based forensic confirmation.

## Investigation Workflow

- Confirm the beacon source host, destination IP or domain, interval characteristics, ports, and alert time window.
- Pivot on the destination indicator to identify whether other internal hosts show the same communication pattern.
- Query asset context for the source system to understand user ownership, business role, environment, and whether the communication pattern is expected.
- Query threat intelligence for the destination infrastructure to assess whether it maps to known malware families, C2 clusters, or recently active campaigns.
- Correlate the beacon with adjacent alerts such as suspicious login, exploit attempts, or credential-related detections to determine whether it is part of a broader attack sequence.

## Logs to Check

- `wazuh-alerts*` for beaconing alerts, IOC matches, suspicious outbound detections, and correlated malware signals
- `security-auditlog*` for nearby identity or privileged access events that may align with the same campaign
- `wazuh-archives*` if populated later for broader raw-event reconstruction
- `wazuh-monitoring*` only to validate logging coverage and timing consistency

## Asset and Intelligence Queries

- Query source-host asset context, owner, exposure, and business function
- Query destination IP/domain intelligence, passive DNS, and malware-infrastructure associations
- Query whether multiple internal assets contacted the same indicator

## Captain Task Tree Initialization

- Create a branch for beacon-timeline reconstruction from `wazuh-alerts*`
- Create a branch for destination-indicator pivoting across peer assets
- Create a branch for source-host context and expected-traffic validation
- Create a branch for threat-intelligence enrichment and campaign correlation

## Decision Focus

- Does the destination have strong malicious reputation?
- Is the beacon isolated to one host or shared across multiple assets?
- Does the broader alert context justify treating the event as active compromise?
