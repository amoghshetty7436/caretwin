# MIMIC-IV Clinical Database Demo v2.2 (subset)

Five tables from the openly available **MIMIC-IV Clinical Database Demo** (100 patients),
used to calibrate CareTwin's patient-flow model:

`hosp/admissions`, `hosp/transfers`, `hosp/services`, `hosp/patients`, `icu/icustays`.

* Source: Johnson, A., Bulgarelli, L., Pollard, T., Horng, S., Celi, L. A., & Mark, R. (2023).
  *MIMIC-IV Clinical Database Demo* (version 2.2). PhysioNet. https://physionet.org/content/mimic-iv-demo/2.2/
* License: Open Data Commons Open Database License v1.0 (see `LICENSE.txt`).
* Checksums in `SHA256SUMS.txt` match the manifest distributed with the dataset.

The demo cohort was sampled from ICU patients, so it over-represents critical care; see
the project README for how the simulator corrects for this. Full MIMIC-IV requires
credentialed PhysioNet access and must **not** be committed to this repository.
