# KidsCrown Probe (Phase 1 + 2 feasibility)

1. Android Studio > New Project > "Empty Views Activity", Kotlin, name KidsCrownProbe,
   package com.kidscrown.probe, Minimum SDK 26.
2. Replace app/src/main/AndroidManifest.xml with AndroidManifest.xml here.
3. Put KidsCrownService.kt and MainActivity.kt in app/src/main/java/com/kidscrown/probe/
   (delete any generated MainActivity first).
4. Create app/src/main/res/xml/ and add accessibility_service_config.xml.
5. Add the service_description string to res/values/strings.xml.
6. In app/build.gradle(.kts) make sure appcompat is a dependency:
   implementation("androidx.appcompat:appcompat:1.7.0")
7. Phone: enable Developer options + USB debugging, plug in, press Run.
8. In the app tap "Enable service", turn on KidsCrown Probe (Android may show a
   restricted-settings warning for sideloaded apps; allow it under App info > menu).
9. Open YouTube, tap videos in each place in the checklist, return to the probe
   and tap Refresh. Logcat filter: tag "KidsCrown".
