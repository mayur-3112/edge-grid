# References — Verification and Correction Record

**Project:** DePIN-Edge: "The Edge Grid" — A Decentralized Physical Infrastructure Network for
Localized, Verifiable AI Inference
**Department of CSE (IoT, Cyber-Security and Blockchain Technology), Sir M. Visvesvaraya Institute of
Technology, Bengaluru — VTU, Belagavi. Academic Year 2026–27.**

**Purpose of this document.** The Phase-1 Literature Survey and the Phase-1 presentation deck both
carry an identical twenty-entry reference list. Every entry in that list has been independently
verified against arXiv, Crossref, DBLP, the ACL Anthology, the ACM and IEEE digital libraries, the
PMLR proceedings and the publishers' own pages. The verification established that **twelve of the
twenty entries were materially incorrect** and that **two entries do not correspond to any real
publication**. Because a reference list that cannot survive an examiner's spot-check discredits the
work it supports, this document supersedes the Phase-1 list. The corrected list in Section 1 is the
list that must be reprinted in the Phase-1 report; the Phase-1 list must not be reused.

**Second-pass reconciliation (Sep. 2026).** Every entry was then re-verified a second time, blind, by
independent reviewers who did not see this document, and the two accounts were reconciled against
primary sources. That pass **confirmed both fabrication findings** ([5] and [7]) and confirmed the
corrections to [1]–[4], [10], [11], [13], [18]–[20]. It also **overturned one finding of this
document**: [16] (PolyLink) was wrongly declared "not peer-reviewed" — it is a genuine IEEE
proceedings paper, and that row has been rewritten. It further found that [12]'s title and author
were still wrong here. Both are fixed below.

The numbering [1]–[20] has been preserved so that the existing in-text citation markers in the
Literature Survey continue to resolve. Two of those numbers, [5] and [7], previously pointed at
publications that do not exist; each has been re-pointed at a genuine source that actually supports
the claim the survey was making. Entries [21]–[29] are additions: systems and results that the
survey's research-gap argument depends on but never examined. Entry [4b] is a further addition — the
peer-reviewed version of [4].

---

## 1. Corrected Reference List (IEEE style)

### Peer-to-peer networking and distributed systems foundations

[1] P. Maymounkov and D. Mazières, "Kademlia: A peer-to-peer information system based on the XOR
metric," in *Peer-to-Peer Systems: First International Workshop (IPTPS 2002)*, Cambridge, MA, USA,
Mar. 2002, Lecture Notes in Computer Science, vol. 2429, Berlin, Germany: Springer, 2002,
pp. 53–65. doi: 10.1007/3-540-45748-8_5.

[2] D. Vyzovitis, Y. Napora, D. McCormick, D. Dias, and Y. Psaras, "GossipSub: Attack-resilient
message propagation in the Filecoin and ETH2.0 networks," Protocol Labs, Technical Report, Jul. 2020.
[Online]. Available: https://arxiv.org/abs/2007.02754. arXiv:2007.02754.

[13] J. Benet, "IPFS — Content addressed, versioned, P2P file system (draft 3)," Protocol Labs,
Technical Report, Jul. 2014. [Online]. Available: https://arxiv.org/abs/1407.3561. arXiv:1407.3561.

[19] J. R. Douceur, "The Sybil attack," in *Peer-to-Peer Systems: First International Workshop
(IPTPS 2002)*, Cambridge, MA, USA, Mar. 2002, Lecture Notes in Computer Science, vol. 2429, Berlin,
Germany: Springer, 2002, pp. 251–260. doi: 10.1007/3-540-45748-8_24.

### Blockchain settlement, data availability and fraud proofs

[3] L. Bousfield, R. Bousfield, C. Buckland, B. Burgess, J. Colvin, E. W. Felten, S. Goldfeder,
D. Goldman, B. Huddleston, H. Kalodner, F. A. Lacs, H. Ng, A. Sanghi, T. Wilson, V. Yermakova, and
T. Zidenberg, "Arbitrum Nitro: A second-generation optimistic rollup," Offchain Labs, Inc.,
Whitepaper, Aug. 2022. [Online]. Available: https://docs.arbitrum.io/nitro-whitepaper.pdf

[4] M. Al-Bassam, A. Sonnino, and V. Buterin, "Fraud and data availability proofs: Maximising light
client security and scaling blockchains with dishonest majorities," University College London and
Ethereum Foundation, Preprint, Sep. 2018 (rev. May 2019). [Online]. Available:
https://arxiv.org/abs/1809.09044. arXiv:1809.09044.

[4b] M. Al-Bassam, A. Sonnino, V. Buterin, and I. Khoffi, "Fraud and data availability proofs:
Detecting invalid blocks in light clients," in *Financial Cryptography and Data Security (FC 2021)*,
Lecture Notes in Computer Science, vol. 12675, Berlin, Germany: Springer, 2021, pp. 279–298.
doi: 10.1007/978-3-662-64331-0_15. — *This is the peer-reviewed version of [4]: retitled, with Ismail
Khoffi added as a fourth author. Cite [4b], not [4], wherever the report needs a refereed source for
the fraud-proof/data-availability result; cite [4] only when quoting the preprint's own wording.*

[20] J. Teutsch and C. Reitwießner, "A scalable verification solution for blockchains," TrueBit
Whitepaper, Nov. 2017; also published in *Aspects of Computation and Automata Theory with
Applications*, Lecture Notes Series, Institute for Mathematical Sciences, National University of
Singapore, vol. 42, Singapore: World Scientific, 2023, pp. 377–424.
doi: 10.1142/9789811278631_0015. Preprint: arXiv:1908.04756.

### Proof of useful work and verifiable computation (replaces the fabricated [5])

[5a] M. Ball, A. Rosen, M. Sabin, and P. N. Vasudevan, "Proofs of useful work," IACR Cryptology
ePrint Archive, Report 2017/203, 2017. [Online]. Available: https://eprint.iacr.org/2017/203

[5b] M. Fitzi, A. Kiayias, G. Panagiotakos, and A. Russell, "Ofelimos: Combinatorial optimization via
proof-of-useful-work — A provably secure blockchain protocol," in *Advances in Cryptology — CRYPTO
2022*, Lecture Notes in Computer Science, vol. 13508, Cham, Switzerland: Springer, 2022,
pp. 339–369. doi: 10.1007/978-3-031-15979-4_12.

[5c] H. Jia, M. Yaghini, C. A. Choquette-Choo, N. Dullerud, A. Thudi, V. Chandrasekaran, and
N. Papernot, "Proof-of-Learning: Definitions and practice," in *Proc. 2021 IEEE Symposium on Security
and Privacy (SP)*, San Francisco, CA, USA, May 2021, pp. 1039–1056.
doi: 10.1109/SP40001.2021.00106.

### Inference runtimes

[6] W. Kwon, Z. Li, S. Zhuang, Y. Sheng, L. Zheng, C. H. Yu, J. E. Gonzalez, H. Zhang, and I. Stoica,
"Efficient memory management for large language model serving with PagedAttention," in *Proc. 29th
ACM Symposium on Operating Systems Principles (SOSP '23)*, Koblenz, Germany, Oct. 2023, pp. 611–626.
doi: 10.1145/3600006.3613165.

[7a] Ollama Contributors, *Ollama* (version 0.x) [Computer software]. Ollama Inc., 2023–.
[Online]. Available: https://github.com/ollama/ollama

[7b] G. Gerganov and llama.cpp Contributors, *llama.cpp: LLM inference in C/C++* [Computer software].
2023–. [Online]. Available: https://github.com/ggml-org/llama.cpp

### LLM evaluation, judging and hallucination benchmarks

[8] L. Zheng, W.-L. Chiang, Y. Sheng, S. Zhuang, Z. Wu, Y. Zhuang, Z. Lin, Z. Li, D. Li, E. P. Xing,
H. Zhang, J. E. Gonzalez, and I. Stoica, "Judging LLM-as-a-Judge with MT-Bench and Chatbot Arena," in
*Advances in Neural Information Processing Systems 36 (NeurIPS 2023), Datasets and Benchmarks
Track*, New Orleans, LA, USA, Dec. 2023. Preprint: arXiv:2306.05685.

[9] S. Lin, J. Hilton, and O. Evans, "TruthfulQA: Measuring how models mimic human falsehoods," in
*Proc. 60th Annual Meeting of the Association for Computational Linguistics (Volume 1: Long
Papers)*, Dublin, Ireland, May 2022, pp. 3214–3252. doi: 10.18653/v1/2022.acl-long.229.

[11] W.-L. Chiang, L. Zheng, Y. Sheng, A. N. Angelopoulos, T. Li, D. Li, B. Zhu, H. Zhang, M. I.
Jordan, J. E. Gonzalez, and I. Stoica, "Chatbot Arena: An open platform for evaluating LLMs by human
preference," in *Proc. 41st International Conference on Machine Learning (ICML 2024)*, Vienna,
Austria, Jul. 2024, PMLR, vol. 235, pp. 8359–8388. Preprint: arXiv:2403.04132.

### Decentralized and edge LLM inference systems

[10] A. Borzunov, D. Baranchuk, T. Dettmers, M. Ryabinin, Y. Belkada, A. Chumachenko, P. Samygin, and
C. Raffel, "Petals: Collaborative inference and fine-tuning of large models," in *Proc. 61st Annual
Meeting of the Association for Computational Linguistics (Volume 3: System Demonstrations)*, Toronto,
Canada, Jul. 2023, pp. 558–568. doi: 10.18653/v1/2023.acl-demo.54.

[10b] A. Borzunov, M. Ryabinin, A. Chumachenko, D. Baranchuk, T. Dettmers, Y. Belkada, P. Samygin,
and C. Raffel, "Distributed inference and fine-tuning of large language models over the Internet," in
*Advances in Neural Information Processing Systems 36 (NeurIPS 2023)*, New Orleans, LA, USA,
Dec. 2023. Preprint: arXiv:2312.08361.

[12] DGrid.AI, "DGrid AI: The decentralized AI inference network for open, low-cost &
community-powered AI," Litepaper, Jun. 2025. [Online]. Available:
https://static.dgrid.ai/dgrid_litepaper.pdf [Accessed: Sep. 3, 2026].

[14] C. Tong, Y. Jiang, G. Chen, T. Zhao, S. Lu, W. Qu, E. Yang, L. Ai, and B. Yuan, "Parallax:
Efficient LLM inference service over decentralized environment," Preprint, Sep. 2025. [Online].
Available: https://arxiv.org/abs/2509.26182. arXiv:2509.26182.

[15] Y. Yang, A. Merlina, W. Song, T. Yuan, K. Birman, and R. Vitenberg, "Navigator: A decentralized
scheduler for latency-sensitive AI workflows," in *Proc. 2024 IEEE International Conference on Edge
Computing and Communications (EDGE)*, Shenzhen, China, Jul. 2024, pp. 35–47.
doi: 10.1109/EDGE62653.2024.00015.

[16] H. Liu, J. Cao, B. Yang, D. Bai, Y. Cao, X. Shen, Y. Zhang, J. Liang, S. Jiang, and M. Zhang,
"PolyLink: A blockchain based decentralized edge AI platform for LLM inference," in *Proc. 2025 IEEE
International Conference on Blockchain (Blockchain)*, 2025, pp. 101–108.
doi: 10.1109/Blockchain67634.2025.00023. Preprint: arXiv:2510.02395.

[17] H. Zhang, Y. Zhao, C. Angione, H. Yang, J. Buban, A. Farhan, F. Johnston, and P. Colangelo,
"Towards secure and private AI: A framework for decentralized inference," in *Proc. NeurIPS 2024
Workshop on Responsibly Building the Next Generation of Multimodal Foundational Models (RBFM)*,
Vancouver, Canada, Dec. 2024. Preprint: arXiv:2407.19401.

[18] Z. Cheng, R. Sun, J. Sun, and Y. Guo, "Scaling decentralized learning with FLock," Preprint,
Jul. 2025 (rev. Aug. 2025). [Online]. Available: https://arxiv.org/abs/2507.15349. arXiv:2507.15349.

---

## 2. Additions — Systems the Survey Must Examine

The Phase-1 survey concludes that "no existing system integrates all five [components] into a
single, open, economically self-sustaining, blockchain-verified, latency-optimized … decentralized AI
inference network." That claim is compared only against Petals, PolyLink, DGrid, Parallax, Navigator,
Nesa and FLock — all of them research artefacts. It is not compared against a single one of the
decentralised-compute networks that are actually in production and selling inference today. A
research-gap claim is only as strong as the systems it was tested against, and an examiner familiar
with the DePIN sector will name Bittensor, Akash or Morpheus within the first minute of questioning.
The following nine references close that exposure.

**Morpheus/Lumerin is the most urgent of these.** It is an Arbitrum-L2-settled AI inference
marketplace in which compute providers post bids that smart contracts match — that is, the exact
mechanism this project claims as its contribution. The project's own repository README (`README.md`,
line 19) and design draft (`docs/PAPER_DRAFT.md`, line 31) already cite it as the reference
implementation for `contracts/`. The Literature Survey has never mentioned it. That gap between what
the code was built from and what the survey admits to reading is the single most damaging
inconsistency in the Phase-1 package, and it must be closed before submission.

[21] Morpheus, Trinity, and Neo (pseudonymous), "Morpheus: A network for powering smart agents,"
Morpheus Whitepaper, Sep. 2, 2023. [Online]. Available:
https://github.com/MorpheusAIs/Docs/blob/main/!KEYDOCS%20README%20FIRST!/WhitePaper.md
[Accessed: Sep. 3, 2026]. *Note: the document is now headed "ARCHIVED: This whitepaper has been
superseded by the modular Diátaxis documentation," and carries its own warning that parts may be
outdated. Cite it for the Sep. 2023 design as stated; for current mechanism detail use the Yellow
Paper in the same repository.*

[22] MorpheusAIs, *Morpheus-Lumerin-Node* [Computer software]. 2024–. ("Enable interaction with
distributed, decentralized LLMs on the Morpheus network through a desktop chat experience.")
[Online]. Available: https://github.com/MorpheusAIs/Morpheus-Lumerin-Node

[23] E. Lui and J. Sun, "Bittensor protocol: The Bitcoin in decentralized artificial intelligence? A
critical and empirical analysis," in *Mathematical Research for Blockchain Economy: 6th International
Conference (MARBLE 2025)*, Athens, Greece, Lecture Notes in Operations Research, Cham, Switzerland:
Springer, 2026, pp. 145–165. doi: 10.1007/978-3-032-13377-9_7. Preprint: arXiv:2507.02951.

[24] Y. Rao, J. Steeves, A. Shaabana, D. Attevelt, and M. McAteer, "BitTensor: A peer-to-peer
intelligence market," Opentensor Foundation, Whitepaper, Mar. 2020 (rev. Nov. 2021; arXiv version
subsequently withdrawn). [Online]. Available: https://bittensor.com/whitepaper

[25] G. Osuri and A. Bozanich, "AKT: Akash network token & mining economics," Akash Network,
Whitepaper, Jan. 31, 2020. [Online]. Available:
https://akash-web-prod.s3.amazonaws.com/uploads/2020/03/akash-econ.pdf

[26] Golem Factory GmbH, "The Golem project: Crowdfunding whitepaper," Nov. 2016. [Online].
Available: https://assets.website-files.com/62446d07873fde065cbcb8d5/62446d07873fdeb626bcb927_Golemwhitepaper.pdf

[27] Gensyn AI Ltd., "Gensyn litepaper: A protocol for verifiable machine learning compute,"
Technical Report, 2022 (legacy edition). [Online]. Available: https://docs.gensyn.ai/litepaper

[28] Z. Lin, T. Wang, L. Shi, S. Zhang, and B. Cao, "Decentralized physical infrastructure network
(DePIN): Challenges and opportunities," Preprint, Jun. 2024. [Online]. Available:
https://arxiv.org/abs/2406.02239. arXiv:2406.02239.

[29] M. S. Andrew and M. C. Ballandies, "Are you a DePIN? A decision tree to classify decentralized
physical infrastructure networks," Preprint, Jan. 2025. [Online]. Available:
https://arxiv.org/abs/2501.17416. arXiv:2501.17416.

---

## 3. Corrections Applied

| Ref | As cited in Phase-1 | Verified fact | Status |
|:--|:--|:--|:--|
| [1] | "in Proc. IPTPS, Springer LNCS vol. 2429, **2020**" | IPTPS **2002**, LNCS 2429, pp. 53–65, doi 10.1007/3-540-45748-8_5 | Wrong year (off by 18 years) |
| [2] | "in Proc. **IEEE International Conference on Peer-to-Peer Computing (P2P), 2022**" | Protocol Labs technical report, arXiv:2007.02754, **Jul. 2020**. IEEE P2P has no 2022 edition — per DBLP its final edition was P2P 2015 | Wrong venue and year; cited venue did not exist |
| [3] | "H. **Kalodner** et al., … Offchain Labs Technical Report, **in Proc. IEEE Blockchain Conference, 2023**" | Offchain Labs whitepaper, **Aug. 2022**; sixteen authors, first author **L. Bousfield** — Kalodner is tenth. No IEEE Blockchain publication exists. (Kalodner *is* first author of the earlier "Arbitrum: Scalable, Private Smart Contracts," USENIX Security 2018 — the two papers appear to have been conflated) | Wrong authors, venue and year |
| [4] | "in Proc. **ACM CCS, 2023**" | arXiv:1809.09044, **Sep. 2018** (rev. May 2019). Never published at ACM CCS | Wrong venue and year |
| [4]† | Survey **table** row 4 describes this as "Celestia: A Modular Data Availability Network … (Al-Bassam et al., **Protocol Labs** / ACM CCS, 2023)" | The reference list and the table describe **two different papers**. The Celestia/LazyLedger design is a separate Al-Bassam work; Al-Bassam is UCL/Celestia Labs, not Protocol Labs | Internal inconsistency; table row must be rewritten to match [4] |
| [5] | "S. Balaji et al., 'Proof-of-Useful-Work: Repurposing Distributed Compute for AI Tasks,' IEEE Blockchain Conference, MIT Digital Currency Initiative, 2023" | **No such publication.** Absent from IEEE Xplore, the MIT DCI publication list, DBLP, Semantic Scholar and Google Scholar. No author "S. Balaji" is associated with any PoUW work. **Re-confirmed independently:** Crossref bibliographic query, OpenAlex title search, an enumeration of all 69 OpenAlex records whose titles contain "proof of useful work" (no Balaji anywhere in the set), a DBLP query returning 0 hits, an arXiv full-text query on the distinctive phrase "Repurposing Distributed Compute" returning 0 results, an exact-phrase web search, and the MIT DCI publications listing — all empty. The venue string is also incoherent: MIT DCI publishes no proceedings | **Fabricated (confirmed twice, independently)** — replaced by [5a], [5b], [5c] |
| [6] | "in Proc. ACM SOSP, 2023" | SOSP '23, Koblenz, pp. 611–626, doi 10.1145/3600006.3613165 | Correct (page range and DOI added) |
| [7] | "T. Eloundou et al., 'Ollama: Democratizing Local LLM Deployment on Consumer Hardware,' Springer AI & Society, vol. 39, no. 2, pp. 544–560, 2024" | **No such publication.** Ollama has never been described in a peer-reviewed paper; the repository has no `CITATION.cff` and issue #10906 is an open request for one. T. Eloundou is a genuine OpenAI researcher whose work ("GPTs are GPTs: Labor market impact potential of LLMs," *Science*, vol. 384, pp. 1306–1308, 2024, doi 10.1126/science.adj0998) concerns labour-market impact and is unrelated to local inference runtimes. **Decisive check:** *AI & SOCIETY* vol. 39, no. 2 is a real issue, but its 39 articles run pp. 433–824 with no gap — the cited pp. 544–560 straddles "Ethical aspects of AI robots for agri-food" (541–555) and "An AI ethics 'David and Goliath'" (557–572). No article occupies that page range. A full Crossref enumeration of the journal, an OpenAlex title search, and a DBLP enumeration of all 23 publications by every author named Eloundou all return nothing | **Fabricated (confirmed twice, independently)** — replaced by software citations [7a], [7b] |
| [8] | "in Proc. NeurIPS, 2023" | NeurIPS 2023 **Datasets and Benchmarks Track**; arXiv:2306.05685 | Correct (track specified) |
| [8]† | Survey **table** row 8 titles this "LLM-as-a-Judge: Using Language Models to Evaluate Language Model Outputs" | Actual title is "Judging LLM-as-a-Judge with MT-Bench and Chatbot Arena" | Table title invented; must match [8] |
| [9] | "in Proc. ACL, 2022" | ACL 2022, pp. 3214–3252, doi 10.18653/v1/2022.acl-long.229 | Correct (pages and DOI added) |
| [10] | "in Proc. **NeurIPS**, 2023" | **ACL 2023 System Demonstrations**, pp. 558–568, doi 10.18653/v1/2023.acl-demo.54. A *different* Borzunov et al. paper — "Distributed Inference and Fine-tuning of LLMs Over The Internet" — was at NeurIPS 2023; the two appear to have been merged | Wrong venue; NeurIPS paper added as [10b] |
| [11] | "in Proc. **ICLR**, 2024" | **ICML 2024**, PMLR vol. 235, pp. 8359–8388 | Wrong venue |
| [12] | "**DGrid Research Team**, 'DGrid AI: The Decentralized AI **Smart** Network,' DGrid Litepaper, June 2025" | Document exists at the cited URL, and "Litepaper – June 2025" is correct. But the title page reads "**DGrid AI: The Decentralized AI Inference Network for Open, Low-Cost & Community-Powered AI**" — verified by reading page 1 of the PDF. "The Decentralized AI Smart Network" is the headline of a third-party Bitget Academy explainer about the DGAI token, not the document's title. The litepaper credits **no authors**; "DGrid Research Team" is an invented attribution — cite DGrid.AI as corporate author | Wrong title; invented author. Also a non-peer-reviewed vendor document tied to a token launch: may support descriptive architecture claims only, never a measured one |
| [13] | "arXiv:1407.3561, **2021**" and, in the table, "arXiv **+ Springer**" | **Jul. 2014**. Never published by Springer; it is a Protocol Labs draft-3 preprint only | Wrong year; false publisher claim |
| [14] | arXiv:2509.26182, 2025 | Confirmed, Sep. 2025, nine authors as listed | Correct (table's title variant "…over Decentralized Heterogeneous GPU Environments" must be corrected to the real title) |
| [15] | "in Proc. IEEE EDGE, 2024" | IEEE EDGE 2024, Shenzhen, pp. 35–47, doi 10.1109/EDGE62653.2024.00015 | Correct (attribution should read Cornell **and University of Oslo**, not Cornell alone) |
| [16] | "arXiv:2510.02395, **IEEE**, 2025" | **This entry was wrongly marked "not peer-reviewed" in an earlier draft of this document; that finding is withdrawn.** PolyLink *was* published: *2025 IEEE International Conference on Blockchain (Blockchain)*, pp. 101–108, doi 10.1109/Blockchain67634.2025.00023 — confirmed by direct Crossref DOI resolution (type `proceedings-article`, publisher IEEE, all ten authors). arXiv:2510.02395 is the preprint of that paper. The cited string is merely incomplete: "IEEE" names a publisher, not a venue | Incomplete venue only. **The survey's "peer-reviewed academic parallel" claim is correct and must be reinstated** |
| [17] | "Nesa Research, in Proc. NeurIPS RBFM Workshop, arXiv:2407.19401, 2024" | Confirmed: NeurIPS 2024 Workshop RBFM poster; arXiv:2407.19401, Jul. 2024 | Correct |
| [18] | "**Y. Chen** et al., 'Scaling Decentralized Learning with FLock: **Blockchain-Based Trust Layer for Collaborative LLM Fine-Tuning**,' arXiv:2507.15349, 2025" | Authors are **Z. Cheng, R. Sun, J. Sun and Y. Guo** — no author named Y. Chen. Real title is "Scaling Decentralized Learning with FLock"; the subtitle after the colon is invented. The 68 % ASR-reduction figure the survey quotes **is genuine** (verified in the paper's abstract and §5) | Wrong authors; invented subtitle |
| [19] | "in Proc. IPTPS, Springer LNCS, **2022**" | IPTPS **2002**, LNCS 2429, pp. 251–260, doi 10.1007/3-540-45748-8_24 | Wrong year (off by 20 years) |
| [19]† | Survey **table** row 19 titles it "The Sybil Attack in Permissionless Peer-to-Peer Networks" and claims it "evaluates proof-of-work, economic staking, and hardware attestation" | Real title is "The Sybil Attack." A 2002 paper predates economic staking and blockchain hardware attestation entirely and discusses none of them | Table title invented; content claim anachronistic and must be removed |
| [20] | "'…: **Optimistic Fraud Proofs**,' **Springer Cryptography, 2022**" | Real title is "A scalable verification solution for blockchains" (the TrueBit whitepaper), Nov. 2017; arXiv:1908.04756, 2019; book-chapter version is **World Scientific**, 2023, pp. 377–424, doi 10.1142/9789811278631_0015 — not Springer | Wrong title, publisher and year |
| [20]† | Survey **table** row 20 titles it "Optimistic Rollup Fraud Proofs: Interactive Verification for Off-Chain Computation" | Not the paper's title | Table title invented |

† = discrepancy between the survey's Table 1 row and its own reference list entry.

**Tally.** Of twenty entries:

- **Six are correct as cited** — [6], [8], [9], [14], [15], [17] (page ranges, DOIs and the NeurIPS
  track are added here for completeness, but nothing was wrong).
- **Twelve carry a wrong venue, year, author list or title** — [1], [2], [3], [4], [10], [11], [12],
  [13], [16], [18], [19], [20].
- **Two are fabricated** — [5], [7].

6 + 12 + 2 = 20. Two further notes that cut across those buckets rather than adding to them:
[12] is a corporate litepaper and must never be cited as peer-reviewed; and [16]'s error runs **in
the student's favour** — it is a genuine peer-reviewed IEEE proceedings paper that the Phase-1 string
undersold, so fixing it strengthens rather than weakens the chapter.

*Table-row title mismatches.* Five are documented above as † rows or inline: [4]†, [8]†, [14], [19]†,
[20]†. **A human must recount these against the original Phase-1 Table 1**, which is no longer
present in this repository (`docs/report/ch4_6_literature.md` has already been rewritten with the
corrected table, and the `.docx` contains only a build of *this* document). An earlier draft asserted
"six"; that figure could not be re-derived from any surviving source and has been replaced with the
five that are individually evidenced.

---

## 4. Consequences for the Argument, and Actions Required

**4.1 The economic premise currently rests on nothing.** Reference [5] is the sole citation for the
project's entire economic model — the claim that idle consumer GPU cycles can be monetised through
verifiable useful work rather than wasteful hashing. That citation is fabricated. The claim itself is
sound and well supported in the real literature, but it must be re-grounded. Ball et al. [5a]
establish that proofs of work can be built on problems of genuine computational interest; Fitzi et
al. [5b] give the first provably secure blockchain protocol whose consensus mechanism is a useful
optimisation solver; and Jia et al. [5c] supply what the project actually needs and [5] merely
claimed — a formal definition of proving that a specific machine-learning computation was performed.
Section 5c is the closest genuine antecedent to the Agentic Verification Module and should be cited
wherever the report currently cites [5].

**4.2 Ollama must be cited as software, not as scholarship.** Ollama has no paper. Any statement in
the report attributed to [7] — Apple Silicon throughput, GGUF quantisation behaviour, TTFT
benchmarks — is unsupported by a citable source and must instead be supported by **this project's own
measurements**, which is in fact the stronger position: the report has real numbers (≈624–730 ms warm
TTFT, ≈9,398 ms cold, CPU-only, no NVIDIA GPU) that no cited paper could supply. Cite [7a] and [7b]
for the software itself and the project's own runlogs for the performance claims.

**4.3 Label each comparator by what it actually is.** Parallax [14] is an arXiv preprint and DGrid
[12] is a corporate litepaper tied to a token launch; neither is peer-reviewed, and any performance,
cost or quality number taken from them must be attributed as the vendor's or authors' own reported
result. **PolyLink [16] is the exception, and an earlier draft of this document got it wrong.** It is
a peer-reviewed paper — *2025 IEEE International Conference on Blockchain*, pp. 101–108,
doi 10.1109/Blockchain67634.2025.00023 — so the survey's description of it as "the closest
peer-reviewed academic parallel" is accurate and should stand. What the Phase-1 string got wrong was
only that it cited the arXiv preprint and wrote "IEEE" where the conference name belongs. The
comparison remains valuable — these are the right systems to compare against — but each must be
labelled by what it is. An examiner who checks one arXiv link and finds no venue will discount every
other claim in the chapter, and an examiner who finds a real IEEE paper dismissed as a preprint will
draw the same conclusion in the other direction.

**4.4 The research-gap claim is not yet defensible.** The "no existing system combines these"
argument was tested against seven research prototypes and zero production networks. Bittensor, Akash,
Golem, Render, io.net, Gensyn and Morpheus all ship some subset of the five pillars today, and
Morpheus [21], [22] ships the specific combination the project claims as novel: Arbitrum L2
settlement plus provider bidding for LLM inference. The honest formulation, which is both defensible
and still a real contribution, is narrower: *no existing open-source system combines Kademlia-based
discovery, GossipSub second-price auctioning, an LLM-as-a-Judge verification pool with on-chain
slashing, and a Merkle-committed data-availability layer in one reproducible reference
implementation.* Lui and Sun [23] additionally provide the empirical finding that Bittensor's rewards
are driven overwhelmingly by stake rather than by output quality — a documented failure mode that
this project's verification-linked payment design can be positioned as a direct response to, which is
a far stronger argument than claiming no prior art exists.

**4.5 Scope divergences that the reference list must not disguise.** Consistency between the
references and the implementation is itself part of the honesty of the report. References [3] and [4]
should not be allowed to imply integrations that were not built. The report's scope table must state
plainly that Arbitrum Stylus (Rust/WASM) was **not** used — the contracts are plain Solidity 0.8.24
on a local Hardhat EVM chain — and that Celestia was **not** integrated; `edgegrid/da.py` is a local
namespaced blob store with real binary Merkle inclusion proofs, a documented stand-in behind the same
interface. References [3] and [4] therefore appear as **design rationale for a settlement and
data-availability architecture**, not as citations for deployed dependencies, and the surrounding
prose must say so.

---

## 5. Verification Status of the Additions, and What Still Needs a Human

**5.1 Additions verified against primary sources (Sep. 3, 2026).** Each of [21]–[29], [4b], and the
replacements [5a]–[5c] and [7a]–[7b] was confirmed to exist as described:

| Ref | How it was confirmed |
|:--|:--|
| [4b] | Crossref DOI 10.1007/978-3-662-64331-0_15 resolves: FC 2021, LNCS, pp. 279–298, four authors incl. Khoffi |
| [5a] | eprint.iacr.org/2017/203 title page: "Proofs of Useful Work," Ball, Rosen, Sabin, Vasudevan |
| [5b] | Crossref DOI 10.1007/978-3-031-15979-4_12: CRYPTO 2022, LNCS, pp. 339–369, four authors as cited |
| [5c] | Crossref DOI 10.1109/sp40001.2021.00106: IEEE S&P 2021, pp. 1039–1056, seven authors as cited |
| [7a] | Repo live. Confirms the fabrication finding: no `CITATION.cff` (HTTP 404), root contains only LICENSE + README; issue #10906 "Recommended Citation Format for Ollama" is **open**, filed 2025-05-30 |
| [7b] | github.com/ggml-org/llama.cpp live; description "LLM inference in C/C++" verbatim |
| [21] | Whitepaper fetched: "Morpheus / A Network For Powering Smart Agents / Authored by Morpheus, Trinity, & Neo / Published - September 2nd 2023" |
| [22] | GitHub API: repo created 2024-03-05, not archived |
| [23] | Crossref DOI 10.1007/978-3-032-13377-9_7: MARBLE, Lecture Notes in Operations Research, pp. 145–165, Springer, Lui & Sun. arXiv:2507.02951 matches |
| [24] | arXiv:2003.03917 confirms the five authors and Mar. 2020. **Withdrawal verified:** v3 (Nov. 10, 2021) states "This paper is incomplete… one of the authors (daniel attevelt) has been removed… this paper is now obsolete" |
| [25] | PDF title page read: "AKT: Akash Network Token & Mining Economics," Greg Osuri, Adam Bozanich, *Akash Network*, **Dated: January 31, 2020**. Corrected here from the earlier "Overclock Labs, Mar. 2020" — March is the upload path, not the document date |
| [26] | PDF title page read: "The Golem Project / Crowdfunding Whitepaper / final version / November 2016" |
| [27] | docs.gensyn.ai/litepaper returns HTTP 200, page title "Litepaper \| Litepaper (legacy) \| Gensyn" — the "legacy edition" label is correct |
| [28] | arXiv:2406.02239, Jun. 4 2024, five authors as cited |
| [29] | arXiv:2501.17416, Jan. 29 2025, Andrew & Ballandies |

**5.2 Do the replacements support the claims they stand in for?** Yes, with one caveat to observe.
[5a] Ball et al. establish proofs of work built on problems of genuine computational interest; [5b]
Fitzi et al. give a provably secure blockchain whose consensus is a useful optimisation solver; [5c]
Jia et al. supply the formal definition of proving that a specific ML computation was performed,
which is the closest genuine antecedent to the Agentic Verification Module. **Caveat:** none of the
three is about *LLM inference* specifically, and [5c] Proof-of-Learning concerns *training*, not
inference. The report must not let [5c] carry a claim about verifying inference work; it supports the
general principle, and the project's own judge-pool design is the inference-specific contribution.
[7a] and [7b] are software citations and support only the existence and identity of the runtimes —
every performance number must come from the project's own runlogs, per §4.2.

**5.3 Still requiring a human check.**

1. ~~**Propagate the [16] reversal.**~~ **Done.** `docs/report/ch4_6_literature.md` carried the
   withdrawn finding in four places — the mischaracterised-sources paragraph, the per-system
   limitation note, the source-standing table row and the reference entry itself. All four now
   state that PolyLink is a refereed IEEE Blockchain 2025 paper, and the chapter records why the
   earlier finding was wrong: an absence from one index is not evidence of absence. The [12] DGrid
   title and its invented "DGrid Research Team" author were corrected in the same pass, and the
   `.docx` has been regenerated. No claim about a source's standing now differs between this file
   and the chapter.
2. **Recount the Phase-1 Table 1 title mismatches.** The original table is not in this repository;
   the "six rows" figure could not be re-derived. Check it against the Phase-1 deck by hand.
3. **[10b] page numbers.** DBLP records no page range for the NeurIPS 2023 paper. An earlier draft
   asserted pp. 12312–12331; that could not be confirmed and has been removed rather than guessed. If
   the Curran Associates printed proceedings are to hand, restore the range from there.
4. **[14] Parallax** was a v1 preprint with no refereed version as of Sep. 2026. Re-check before
   final submission in case one has appeared — the same is now known to have happened to [16].
5. **[12] DGrid** is served from a file regenerated after its stated June 2025 date (embedded PDF
   creation date Aug. 2026) while still bearing the June 2025 title page. The accessed date is
   recorded above for that reason.
