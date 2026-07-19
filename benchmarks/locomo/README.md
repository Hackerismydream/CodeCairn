# LoCoMo Adapter

CodeCairn evaluates end-to-end memory question answering against the public
ten-conversation LoCoMo release. The dataset remains an external input because
it is licensed CC BY-NC 4.0; it is not redistributed in this repository.

Download and verify the pinned release:

```bash
mkdir -p benchmarks/locomo/data
curl -fsSL \
  https://raw.githubusercontent.com/snap-research/locomo/main/data/locomo10.json \
  -o benchmarks/locomo/data/locomo10.json
echo "79fa87e90f04081343b8c8debecb80a9a6842b76a7aa537dc9fdf651ea698ff4  benchmarks/locomo/data/locomo10.json" \
  | shasum -a 256 -c -
```

The loader preserves conversation identifiers, session boundaries, original
session timestamps, speakers, questions, categories, evidence dialog IDs, gold
answers, and adversarial annotations. Text and available image captions become
attributed exact-quote memories through `MemoryRuntime.evaluate_proposal`; each
conversation uses its own runtime root.

The default scored protocol includes categories 1 through 4 and retains but
does not score category 5. This follows the declared CodeCairn protocol rather
than silently treating missing category-5 `answer` fields as failures. Category
mapping is recorded in every report. Smoke runs answer one question per selected
conversation, perform no judge calls, and are always marked unscored.

LoCoMo was introduced by Maharana et al., “Evaluating Very Long-Term
Conversational Memory of LLM Agents,” ACL 2024. See the
[upstream repository](https://github.com/snap-research/locomo) and
[paper](https://aclanthology.org/2024.acl-long.747/).
