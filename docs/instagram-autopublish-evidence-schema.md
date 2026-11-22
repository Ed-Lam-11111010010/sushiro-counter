# Instagram auto-publish evidence schema

Schema version 1 documents the policy file and optional OCR evidence accepted by `scripts/instagram_autopublish.py`. The detector output remains review-only. The policy engine creates a verified promotion preview only after re-fetching the public post and reproducing its author, fingerprint, price and item evidence.

## Policy

`data/instagram_autopublish_policy.json` is the single policy configuration. Its important fields are:

| Field | Meaning |
| --- | --- |
| `policyVersion` | Audited policy identifier copied to generated promotion evidence. |
| `productionEnabled` | Must be `true` in addition to workflow gates before a production write. Checked in as `false`. |
| `killSwitch` | Must be `false`; checked in as `true`. |
| `approvedPolicyVersion` | Must exactly match `policyVersion`; checked in as `null`. |
| `sourceAccount` | Required public author account, currently `sushiro.hk`. |
| `priceBoundsHKD` | Positive minimum and maximum HKD values. |
| `minimumOcrConfidence` | Minimum OCR confidence, from 0 through 1. Current threshold is `0.99`. |
| `trustedOcrProviders` | Exact OCR provider identifiers allowed for evaluation. Empty in the checked-in policy because no OCR engine has been integrated or independently validated. |
| `maximumEvidenceAgeHours` | Maximum age of the local/runner observation timestamp. |
| `maximumItemsPerRun`, `maximumPromotionsPerRun` | Stop a batch that exceeds the reviewed change limit. |
| `fuzzyNameMatchThreshold` | Similarity threshold that quarantines possible duplicate names. |
| `requireCompleteCarouselOcr` | If public images are exposed, incomplete or absent carousel OCR quarantines the affected item. |
| `protectedPostIds`, `protectedPromotionIds`, `protectedItemIds`, `protectedItemNames` | Explicit collision and PR #10 protection lists. |

## Candidate input

The scanner document is a JSON object with `schemaVersion: 1`, `account`, `profileFetchStatus`, `postDiscoveryStatus`, `sourceStatus`, `discoveryComplete`, `discoveryCoverage`, and `posts`. Automatic eligibility additionally requires `discoveryComplete: true`, `discoveryCoverage: "public-html-best-effort-not-exhaustive"`, and `postDiscoveryStatus: "found"`. `discoveryComplete` means the currently accessible public HTML sources were read without known failures; it does not prove that Instagram exposed every account post. Partial, unavailable, manually supplied, empty, or incomplete scans cannot publish.

Each post records a canonical Instagram `url`, `postId`, SHA-256 `contentFingerprint`, `observedAt`, optional `publishedAt`, `imageUrls`, and extracted `items`. The detector adds `authorEvidence`, `discoverySources`, and `extractionVersion` for review. Each item may include `name`, `itemType`, `purchaseCondition`, `price`, `currency`, `priceConfidence`, `priceEvidenceSource`, `priceEvidenceQuote`, `startDate`, `endDate`, `dateConfidence`, `dateEvidenceText`, `supplyCondition`, `availabilityEvidenceText`, `branchScope`, and `branchEvidenceText`.

The evidence policy accepts only `dish` or `drink`, `standard` purchase conditions, explicit positive HKD amounts, a confidently associated start date, and evidence for supply and all-Hong-Kong branch scope. Merchandise, threshold purchases, branch restrictions, unknown stock/end conditions, conflicts, and duplicates are quarantined. A date may have `endDate: null` only when the evidence explicitly states an open-ended or until-sold-out condition.

## Optional OCR sidecar

The OCR sidecar uses `schemaVersion: 1` and a `posts` array. The sidecar is supplied by an external OCR process; this repository does not contain an OCR model or claim to read menu text from images automatically.

```json
{
  "schemaVersion": 1,
  "posts": [
    {
      "postId": "POST_ID",
      "provider": "reviewed-ocr-provider/version",
      "fingerprint": "64-character candidate content fingerprint",
      "coverageComplete": true,
      "images": [
        {
          "url": "https://scontent.cdninstagram.com/public-image.jpg",
          "status": "complete",
          "confidence": 0.999,
          "sha256": "64-character SHA-256 of the fetched image bytes"
        }
      ],
      "items": [
        {
          "name": "限定壽司",
          "itemType": "dish",
          "purchaseCondition": "standard",
          "confidence": 0.999,
          "nameEvidenceText": "限定壽司",
          "price": 22,
          "priceEvidenceText": "HK$22",
          "imageUrl": "https://scontent.cdninstagram.com/public-image.jpg",
          "imageSha256": "same SHA-256 as the matching image record",
          "startDate": "2026-11-20",
          "endDate": "2026-11-30",
          "dateConfidence": "high-confidence-ocr",
          "dateEvidenceText": "2026-11-20至2026-11-30",
          "branchScope": "all-hk",
          "branchEvidenceText": "全港各分店有售"
        }
      ]
    }
  ]
}
```

Confidence values must be finite numbers between the configured minimum and `1`. The OCR `provider` must exactly match an ID in `trustedOcrProviders`; the checked-in empty list means OCR-derived items are currently quarantined. Every exposed image must have a complete OCR record and an image SHA-256 that the policy engine independently recomputes from the public image bytes. The item image hash must match that record. The engine only fetches HTTPS URLs from Instagram/CDN hostnames exposed by the public page, caps each response at 12 MiB, and rejects redirects outside the same host allowlist. A wrong hash, missing image, incomplete carousel, low confidence, ambiguous currency, or caption/OCR conflict is a quarantine reason.

Generated promotions retain the canonical source post URL, source fingerprint, policy version, item evidence, and dates. Generation is append-only: verified catalog entries and stable IDs are never rewritten. Production writes require both CLI opt-in and all policy/environment/workflow gates; repository settings and approvals are managed separately.
