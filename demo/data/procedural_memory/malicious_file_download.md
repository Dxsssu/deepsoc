---
id: proc-malicious-file-download-md
title: Malicious File Download Network Traceback SOP
alert_type: malicious file download
aliases:
  - malware download
  - suspicious download
  - payload retrieval
tags:
  - malware
  - download
  - network traceback
---

# Malicious File Download Network Traceback SOP

## Scope

This SOP is limited to network-level traceback. The workflow should rely on alert streams, audit logs, asset context, and threat intelligence. It should not assume local file-system or hash triage beyond what is already present in alerts.

## Investigation Workflow

- Confirm the source host, download URL or domain, time window, and any alert-provided file or reputation metadata.
- Pivot on the URL, domain, and source asset to determine whether the same payload source touched additional hosts or repeated sessions.
- Query asset context for the downloading host to determine owner, exposure, role, and whether the download behavior is expected.
- Query threat intelligence on the URL, domain, hosting infrastructure, and any file hash indicators surfaced by alerts.
- Correlate the download with phishing, suspicious outbound, or credential-related alerts to determine whether the file retrieval is part of a broader attack chain.

## Logs to Check

- `wazuh-alerts*` for malicious download detections, IOC matches, and malware-related alert chaining
- `security-auditlog*` for identity or administrative events near the same time window
- `wazuh-archives*` if populated later for raw-event reconstruction

## Asset and Intelligence Queries

- Query source-host asset context and expected business-download profile
- Query whether peer hosts contacted the same URL or domain
- Query threat intelligence on URLs, domains, and hash-related indicators

## Captain Task Tree Initialization

- Create a branch for payload-source timeline reconstruction
- Create a branch for source-host and peer-host pivoting
- Create a branch for asset-context and user-context review
- Create a branch for threat-intelligence enrichment and follow-on alert correlation

## Decision Focus

- Is the download source clearly malicious or only suspicious?
- Did multiple assets retrieve the same payload source?
- Does adjacent context suggest the file retrieval is part of a compromise path?
