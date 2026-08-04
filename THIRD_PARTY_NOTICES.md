# Third-party notices

## Inter typeface

The dashboard bundles Inter 4.1 webfont files from:

<https://github.com/rsms/inter/releases/tag/v4.1>

Inter is Copyright 2016 The Inter Project Authors and is distributed under the SIL Open Font License 1.1. The complete license text is included at `app/static/media/Inter-LICENSE.txt`.

## Playwright Docker seccomp profile

`seccomp_profile.json` is copied from:

<https://github.com/microsoft/playwright/blob/main/utils/docker/seccomp_profile.json>

It is used only by the optional `docker-compose.browser.yml` configuration to enable Chromium user namespaces/sandboxing for research against untrusted public websites. Playwright is distributed under the Apache License 2.0; see the upstream repository for the complete license and notices:

<https://github.com/microsoft/playwright>

## llama.cpp optional runtime

The optional Docker backend uses official images from:

<https://github.com/ggml-org/llama.cpp>

llama.cpp is distributed under the MIT License. The project is not vendored or modified in this ZIP; Docker pulls the selected official server image at deployment time. Model weights have their own licenses, which must be reviewed before use.
