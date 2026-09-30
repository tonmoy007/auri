# Priest retrieval report

Generated 2026-09-30T19:23:49Z from 50 gold questions. Recall is over in-scope notes; BM25, dense and hybrid are compared.

**These figures are historical.** They were produced before commit d4db828, which changed both the app and this evaluation: the app's "covered" decision is now the best cosine, or the best BM25 score over the chunks actually returned using a note's best 4 matching terms (it was the best raw scores before any cap), and `backend/scripts/eval_priest_retrieval.py` now scores the hybrid at the app's chunk-level `top_k` (the tables below were not scored that way) and gates on the configured F1. The recall, MRR and F1 figures below must be regenerated on a machine with enough memory (user action); no new numbers are given here.

**The calibrated F1 is in-sample.** The calibrated floors are fitted on the same 50 questions (10 of them out-of-scope negatives) that the F1 is then scored on, so it describes this data and is not a forecast of how the floors will do on new questions. The "configured" rows use the floors from the settings at the time and are the ones the gate should judge.

## Embedder comparison

| index | embed model | hybrid recall@6 | hybrid MRR | calibrated F1 | hybrid p95 ms |
| --- | --- | --- | --- | --- | --- |
| 1 | nomic-embed-text | 0.804 | 0.663 | 0.706 | 34.3 |
| 2 | mxbai-embed-large | 0.825 | 0.691 | 0.824 | 50.3 |
| 3 | bge-large | 0.817 | 0.708 | 0.842 | 37.0 |

## Index 1: nomic-embed-text

Version 20260930T190624Z-ca94f68e, 6698 chunks.

| mode | recall@3 | recall@6 | recall@10 | MRR | p50 ms | p95 ms |
| --- | --- | --- | --- | --- | --- | --- |
| bm25 | 0.575 | 0.662 | 0.671 | 0.585 | 0.4 | 1.7 |
| dense | 0.637 | 0.696 | 0.742 | 0.679 | 23.5 | 34.2 |
| hybrid | 0.675 | 0.804 | 0.812 | 0.663 | 25.9 | 34.3 |

Not-covered detection (precision, recall and F1 of *not covered*):

| floors | dense floor | BM25 floor | precision | recall | F1 |
| --- | --- | --- | --- | --- | --- |
| configured | 0.5 | 3.0 | 0.0 | 0.0 | 0.0 |
| calibrated | 0.608 | 12.15 | 0.857 | 0.6 | 0.706 |

Gate [A] (hybrid recall@6 >= 0.85, calibrated F1 >= 0.85): FAIL.

Pin these settings for this index:

    PRIEST_EMBED_MODEL=nomic-embed-text
    PRIEST_MIN_RELEVANCE_DENSE=0.608
    PRIEST_MIN_RELEVANCE_BM25=12.15

In-scope questions the hybrid missed at recall@6 (ids): g15, g16, g21, g23, g27.

## Index 2: mxbai-embed-large

Version 20260930T190939Z-1e5226ff, 6698 chunks.

| mode | recall@3 | recall@6 | recall@10 | MRR | p50 ms | p95 ms |
| --- | --- | --- | --- | --- | --- | --- |
| bm25 | 0.575 | 0.662 | 0.671 | 0.585 | 0.3 | 1.3 |
| dense | 0.721 | 0.808 | 0.858 | 0.762 | 26.6 | 65.6 |
| hybrid | 0.646 | 0.825 | 0.871 | 0.691 | 29.0 | 50.3 |

Not-covered detection (precision, recall and F1 of *not covered*):

| floors | dense floor | BM25 floor | precision | recall | F1 |
| --- | --- | --- | --- | --- | --- |
| configured | 0.5 | 3.0 | 0.0 | 0.0 | 0.0 |
| calibrated | 0.534 | 18.8 | 1.0 | 0.7 | 0.824 |

Gate [A] (hybrid recall@6 >= 0.85, calibrated F1 >= 0.85): FAIL.

Pin these settings for this index:

    PRIEST_EMBED_MODEL=mxbai-embed-large
    PRIEST_MIN_RELEVANCE_DENSE=0.534
    PRIEST_MIN_RELEVANCE_BM25=18.8

In-scope questions the hybrid missed at recall@6 (ids): g15, g16, g23, g28, g37.

## Index 3: bge-large

Version 20260930T191628Z-92ee3854, 6698 chunks.

| mode | recall@3 | recall@6 | recall@10 | MRR | p50 ms | p95 ms |
| --- | --- | --- | --- | --- | --- | --- |
| bm25 | 0.575 | 0.662 | 0.671 | 0.585 | 0.3 | 1.4 |
| dense | 0.729 | 0.821 | 0.829 | 0.76 | 25.9 | 32.1 |
| hybrid | 0.654 | 0.817 | 0.879 | 0.708 | 28.8 | 37.0 |

Not-covered detection (precision, recall and F1 of *not covered*):

| floors | dense floor | BM25 floor | precision | recall | F1 |
| --- | --- | --- | --- | --- | --- |
| configured | 0.5 | 3.0 | 0.0 | 0.0 | 0.0 |
| calibrated | 0.547 | 18.8 | 0.889 | 0.8 | 0.842 |

Gate [A] (hybrid recall@6 >= 0.85, calibrated F1 >= 0.85): FAIL.

Pin these settings for this index:

    PRIEST_EMBED_MODEL=bge-large
    PRIEST_MIN_RELEVANCE_DENSE=0.547
    PRIEST_MIN_RELEVANCE_BM25=18.8

In-scope questions the hybrid missed at recall@6 (ids): g23, g27, g28, g37.
