---
id: proc-suspicious-outbound-md
title: Suspicious Outbound Traffic Network Traceback SOP
alert_type: suspicious outbound traffic
aliases:
  - suspicious outbound
  - c2 traffic
  - suspicious egress
tags:
  - network
  - outbound
  - threat intelligence
---

# Suspicious Outbound Traffic Network Traceback SOP

## Scope

This SOP is limited to network-level traceback. The workflow should rely on alert logs, audit logs, asset context, and threat-intelligence pivots. It should not assume packet capture forensics or host-resident malware analysis.

## Investigation Workflow

- Confirm the source asset, destination IP or domain, protocol, port, frequency, and alert time window.
- Pivot on the destination indicator to determine whether other internal assets communicated with the same endpoint in the same or nearby time window.
- Query asset context for the source system to determine business role, owner, criticality, and whether the destination pattern is expected.
- Query threat intelligence on the destination IP, domain, ASN, and related infrastructure to assess whether the traffic is linked to C2, malware hosting, phishing, or known malicious campaigns.
- Correlate the event with other network-visible alerts such as suspicious login, credential access, or exploit activity to determine whether the outbound traffic is part of a broader intrusion chain.

## Logs to Check

- `wazuh-alerts*` for outbound anomaly alerts, IOC matches, and suspicious network detections
- `security-auditlog*` for identity or access activity near the outbound event window
- `wazuh-archives*` if populated later for raw-event reconstruction
- `wazuh-monitoring*` only to validate telemetry continuity

## Asset and Intelligence Queries

- Query asset inventory for the outbound source host, owner, environment, and business purpose
- Query whether peer assets show similar outbound behavior
- Query destination reputation, passive DNS context, and related threat-intelligence tags

## Captain Task Tree Initialization

- Create a branch for outbound timeline reconstruction from `wazuh-alerts*`
- Create a branch for destination-indicator pivoting across peer assets
- Create a branch for source-asset context review and expected-business-validation
- Create a branch for threat-intelligence enrichment on outbound infrastructure

## Decision Focus

- Is the destination known malicious or merely unusual?
- Is the traffic isolated to one host or shared by multiple hosts?
- Does the source asset context make the outbound activity suspicious enough to escalate?
