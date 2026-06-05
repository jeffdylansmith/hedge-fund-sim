"""
Fine-tune Llama 3.1 8B on Meridian Capital trading decisions using LoRA.

WHAT IT DOES
------------
Loads instruction-tuning pairs exported from GET /training/export and fine-tunes
a Llama 3.1 8B Instruct model using LoRA (Low-Rank Adaptation). The result is a
small adapter (~50MB) that can be loaded on top of the base model to replicate a
specific trader's decision-making style.

HOW TO RUN
----------
# Export training data first:
  curl https://<your-api>/training/export > training_data.jsonl
  # or profitable trades only:
  curl https://<your-api>/training/export?outcome=profitable > training_data.jsonl

# Dry run (no GPU needed — validates data and prints examples):
  python scripts/finetune.py --data training_data.jsonl --dry-run

# Full fine-tune for one trader:
  python scripts/finetune.py --data training_data.jsonl --trader alex --output models/alex/

# All traders combined:
  python scripts/finetune.py --data training_data.jsonl --output models/meridian/

REQUIREMENTS
------------
  pip install transformers peft datasets accelerate bitsandbytes torch

GPU: 8GB+ VRAM recommended (A10G, RTX 3080, etc.)
  - With fp16 and batch_size=4, peak VRAM ~7GB
  - A10G on RunPod spot: ~$0.60/hr → $3-5 for a full run

ESTIMATED TRAINING TIME (A10G spot)
------------------------------------
  100 examples  → ~5 minutes
  500 examples  → ~20 minutes
  2000 examples → ~1 hour
"""

import argparse
import json
import sys
from pathlib import Path


def load_data(path: str, trader: str | None) -> list[dict]:
    examples = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if trader and obj.get("metadata", {}).get("trader_id") != trader:
                continue
            examples.append(obj)
    return examples


def format_for_chat(example: dict, tokenizer) -> str:
    """Format one example as a chat-template string for the model."""
    messages = [
        {"role": "user", "content": example["instruction"]},
        {"role": "assistant", "content": example["response"]},
    ]
    return tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=False)


def dry_run(examples: list[dict]) -> None:
    outcome_counts = {"profitable": 0, "unprofitable": 0, "open": 0}
    for e in examples:
        o = e.get("metadata", {}).get("outcome", "open")
        outcome_counts[o] = outcome_counts.get(o, 0) + 1

    total = len(examples)
    print(f"\nDry run — data summary")
    print(f"{'='*50}")
    print(f"Total training examples : {total}")
    print(f"  Profitable trades     : {outcome_counts['profitable']}")
    print(f"  Unprofitable trades   : {outcome_counts['unprofitable']}")
    print(f"  Open positions        : {outcome_counts['open']}")

    # Rough estimate: ~60 tokens/example, A10G does ~2500 tokens/sec training throughput
    tokens_per_example = 60
    tokens_per_sec = 2500
    epochs = 3
    est_seconds = (total * tokens_per_example * epochs) / tokens_per_sec
    print(f"\nEstimated training time (A10G, 3 epochs): ~{est_seconds/60:.0f} minutes")

    print(f"\nFirst 3 examples as fed to the model:")
    print(f"{'='*50}")
    for i, ex in enumerate(examples[:3]):
        print(f"\n[Example {i+1}]")
        print(f"  INSTRUCTION: {ex['instruction']}")
        print(f"  RESPONSE:    {ex['response']}")
        meta = ex.get("metadata", {})
        print(f"  METADATA:    trader={meta.get('trader_id')} ticker={meta.get('ticker')} outcome={meta.get('outcome')}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Fine-tune Llama 3.1 8B on Meridian trading data")
    parser.add_argument("--data", required=True, help="Path to training_data.jsonl exported from /training/export")
    parser.add_argument("--trader", default=None, help="Filter to one trader (alex|jordan|casey). Omit for all.")
    parser.add_argument("--output", required=True, help="Output directory for LoRA adapter")
    parser.add_argument("--base-model", default="meta-llama/Llama-3.1-8B-Instruct", help="Base model to fine-tune")
    parser.add_argument("--epochs", type=int, default=3, help="Number of training epochs")
    parser.add_argument("--dry-run", action="store_true", help="Validate data and print stats without training")
    args = parser.parse_args()

    examples = load_data(args.data, args.trader)
    if not examples:
        print(f"No training examples found (trader filter: {args.trader!r})", file=sys.stderr)
        sys.exit(1)

    if args.dry_run:
        dry_run(examples)
        return

    # Import heavy deps only when actually training
    from transformers import AutoModelForCausalLM, AutoTokenizer, TrainingArguments, Trainer
    from peft import LoraConfig, get_peft_model, TaskType
    from datasets import Dataset

    print(f"Loading base model: {args.base_model}")
    tokenizer = AutoTokenizer.from_pretrained(args.base_model)
    tokenizer.pad_token = tokenizer.eos_token

    model = AutoModelForCausalLM.from_pretrained(
        args.base_model,
        torch_dtype="auto",
        device_map="auto",
    )

    lora_config = LoraConfig(
        r=16,
        lora_alpha=32,
        target_modules=["q_proj", "v_proj"],
        lora_dropout=0.05,
        task_type=TaskType.CAUSAL_LM,
    )
    model = get_peft_model(model, lora_config)
    model.print_trainable_parameters()

    print(f"Formatting {len(examples)} examples...")
    formatted = [format_for_chat(ex, tokenizer) for ex in examples]
    tokenized = tokenizer(formatted, truncation=True, padding=True, max_length=512, return_tensors="pt")

    dataset = Dataset.from_dict({
        "input_ids": tokenized["input_ids"],
        "attention_mask": tokenized["attention_mask"],
        "labels": tokenized["input_ids"],
    })

    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    training_args = TrainingArguments(
        output_dir=str(output_dir),
        num_train_epochs=args.epochs,
        per_device_train_batch_size=4,
        save_steps=100,
        logging_steps=10,
        learning_rate=2e-4,
        fp16=True,
        report_to="none",
    )

    trainer = Trainer(
        model=model,
        args=training_args,
        train_dataset=dataset,
    )

    print(f"Training on {len(dataset)} examples for {args.epochs} epochs...")
    result = trainer.train()
    final_loss = result.training_loss

    model.save_pretrained(str(output_dir))
    tokenizer.save_pretrained(str(output_dir))

    print(f"\nTraining complete.")
    print(f"  Final loss    : {final_loss:.4f}")
    print(f"  Adapter saved : {output_dir}/")


if __name__ == "__main__":
    main()
