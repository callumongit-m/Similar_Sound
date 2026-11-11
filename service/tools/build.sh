#!/bin/bash
# Builds the audio helper next to its source. The service also builds it on first use.
cd "$(dirname "$0")" && swiftc -O -swift-version 5 audiotap.swift -o audiotap && echo "built tools/audiotap"
