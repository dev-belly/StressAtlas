# Sources and contribution boundaries

## Curve implementation

- Upstream: [luphord/nelson_siegel_svensson](https://github.com/luphord/nelson_siegel_svensson).
- Reviewed commit: `0cc9aea682e3e007d6cf08e028e07e7ca5157ed6`, version 0.5.0.
- MIT copyright: luphord, 2019; full notice retained in [NSS-LICENSE](NSS-LICENSE).
- Used as a pinned dependency. Curve evaluation and the underlying NSS formula
  remain credited to upstream. The upstream repository's stars and history are
  not presented as adoption or contributions to this application.

## Bond engine

[QuantLib](https://www.quantlib.org/), version 1.44, under its
[modified BSD license](https://www.quantlib.org/license.shtml), supplies the
cash-flow schedule and discounting-bond engine. It is a dependency, not code
authored by this project.

## Historical curve estimates

Primary attribution: Board of Governors of the Federal Reserve System;
Refet S. Gürkaynak, Brian Sack and Jonathan H. Wright, *The U.S. Treasury Yield
Curve: 1961 to the Present* (2006 working paper / 2007 publication).

- [Primary data and methodology](https://www.federalreserve.gov/data/nominal-yield-curve.htm).
- [Source paper](https://www.federalreserve.gov/pubs/feds/2006/200628/200628pap.pdf).
- [Board copyright and attribution statement](https://www.federalreserve.gov/disclaimer.htm).
- Actual downloaded mirror: `micla1/term-premium-python`, pinned commit
  `26e50238532cea42ef594d91189082a56425273d`, `data/feds200628.csv`.
- Original-file SHA-256:
  `5c85473f8244b21ebe6eea16511ce4166946b6f0a2b1532bf340f3b063021dde`.
- [Complete machine-readable source ledger](src/curveshift/data/source.json)
  records the exact download URL, retrieval time, subset hash, column selection
  and all excluded source dates.

The Board states that its information is generally public domain unless
otherwise indicated and requests source attribution. Its data and name are not
relicensed as original project code; no Board seal or endorsement is used.

The primary CSV download was unavailable to this runtime. The stored data were
therefore acquired from the identified public mirror; no direct-primary-download
or independent source-authentication claim is made. No code from that mirror
repository was copied. The original CSV describes these as staff research
estimates, subject to revision, rather than an official statistical release.

## Additions in this application

dev-belly's additions include the frozen-data adapter, QuantLib/discount-sum
comparison, key-rate DV01 and gamma, duration-neutral counterexample, historical
full-revaluation risk, empirical tail-mass ES, moving-block resampling,
chronological PCA study, scenario replay, API, interactive report and tests.
The financial formulas and named methods retain their established origins;
this is an application and research implementation, not a claim to invent them.
