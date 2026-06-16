#!/bin/bash

pandoc $1 \
  --standalone \
  --toc \
  -t epub3 \
  -o $2