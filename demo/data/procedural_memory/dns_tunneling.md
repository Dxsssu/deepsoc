---
id: proc-dns-tunneling-md
title: DNS Tunneling Network Traceback SOP
alert_type: dns tunneling
aliases:
  - dns exfiltration
  - suspicious dns
  - dns c2
tags:
  - dns
  - tunneling
  - network traceback
---

# DNS Tunneling Network Traceback SOP

## Scope

This SOP is limited to network-level traceback. The workflow should rely on DNS-visible alerts, audit logs, asset context, and threat intelligence. It should not assume packet payload decryption or host-based malware triage.

## Investigation Workflow

- Confirm the source asset, queried domains, request rate, query length pattern, and alert time window.
- Pivot on the suspicious domain, subdomain pattern, or resolver path to determine whether other internal assets are involved.
- Query asset context for the source system to determine business role, owner, and whether high-volume or unusual DNS activity is expected.
- Query threat intelligence on the domain, NS records, hosting provider, and historical reputation to assess likely maliciousness.
- Correlate the DNS activity with suspicious outbound traffic, beaconing, or phishing indicators to determine whether the event is part of a broader attack chain.

## Logs to Check

- `wazuh-alerts*` for suspicious DNS, beaconing, or tunneling-related detections
- `security-auditlog*` for adjacent identity or administrative events near the same time window
- `wazuh-archives*` if populated later for broader raw-event reconstruction

## Asset and Intelligence Queries

- Query source-host asset context, owner, and expected business DNS profile
- Query domain reputation, passive DNS, registrar context, and related infrastructure
- Query whether additional hosts resolved the same suspicious domains

## Captain Task Tree Initialization

- Create a branch for DNS timeline reconstruction and subdomain-pattern analysis
- Create a branch for domain and resolver pivoting across peer assets
- Create a branch for source-host context and expected-traffic validation
- Create a branch for threat-intelligence enrichment on the suspicious domain infrastructure

## Decision Focus

- Does the domain pattern look algorithmic, encoded, or beacon-like?
- Are multiple internal hosts resolving the same suspicious domain?
- Does the surrounding context suggest command-and-control or data exfiltration intent?
