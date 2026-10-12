# AutoPost Automation 3.0 - created by Mr Blank (github.com/pavelblank/autopost-automation). Keep this notice.
r"""Convert Wan2.1 original umt5 .pth (custom keys) -> HF UMT5EncoderModel (diffusers text_encoder/).
Source: models_t5_umt5-xxl-enc-bf16.pth (already downloaded, 10.8 GB bf16).
Target: models\Wan2.1-T2V-1.3B-Diffusers\text_encoder\
Saves re-downloading 21.6 GB. Strict load — any key/shape mismatch aborts loudly.
"""
import re, sys, time, urllib.request
from pathlib import Path
import torch

SRC = Path(__file__).resolve().parents[2] / "models" / "Wan2.1-T2V-1.3B" / "models_t5_umt5-xxl-enc-bf16.pth"
TE_DIR = Path(__file__).resolve().parents[2] / "models" / "Wan2.1-T2V-1.3B-Diffusers" / "text_encoder"
CFG_URL = "https://huggingface.co/Wan-AI/Wan2.1-T2V-1.3B-Diffusers/raw/main/text_encoder/config.json"

t0 = time.time()
TE_DIR.mkdir(parents=True, exist_ok=True)

# 1) config.json
cfg_path = TE_DIR / "config.json"
if not cfg_path.exists():
    print("[cfg] downloading text_encoder/config.json ...", flush=True)
    urllib.request.urlretrieve(CFG_URL, cfg_path)
print("[cfg] ok", flush=True)

from transformers import UMT5EncoderModel, AutoConfig

cfg = AutoConfig.from_pretrained(TE_DIR)

# 2) instantiate target model without allocating fp32 (bf16 or meta)
def build():
    try:
        m = UMT5EncoderModel.from_config(cfg, dtype=torch.bfloat16)
        print("[model] built bf16 via from_config(dtype=)", flush=True)
        return m, False
    except Exception as e:
        print("[model] from_config(dtype=) failed:", e, flush=True)
    try:
        m = UMT5EncoderModel.from_config(cfg, torch_dtype=torch.bfloat16)
        print("[model] built bf16 via from_config(torch_dtype=)", flush=True)
        return m, False
    except Exception as e:
        print("[model] from_config(torch_dtype=) failed:", e, flush=True)
    with torch.device("meta"):
        m = UMT5EncoderModel(cfg)
    print("[model] built on meta device", flush=True)
    return m, True  # needs assign=True on load

model, is_meta = build()

# 3) map keys
def to_hf(k):
    if k == "token_embedding.weight":
        return "shared.weight"
    if k == "norm.weight":
        return "encoder.final_layer_norm.weight"
    m = re.match(r"^blocks\.(\d+)\.(.+)$", k)
    if not m:
        return None
    n, rest = m.group(1), m.group(2)
    tbl = {
        "norm1.weight":      "encoder.block.{n}.layer.0.layer_norm.weight",
        "norm2.weight":      "encoder.block.{n}.layer.1.layer_norm.weight",
        "attn.q.weight":     "encoder.block.{n}.layer.0.SelfAttention.q.weight",
        "attn.k.weight":     "encoder.block.{n}.layer.0.SelfAttention.k.weight",
        "attn.v.weight":     "encoder.block.{n}.layer.0.SelfAttention.v.weight",
        "attn.o.weight":     "encoder.block.{n}.layer.0.SelfAttention.o.weight",
        "pos_embedding.embedding.weight": "encoder.block.{n}.layer.0.SelfAttention.relative_attention_bias.weight",
        "ffn.gate.0.weight": "encoder.block.{n}.layer.1.DenseReluDense.wi_0.weight",
        "ffn.fc1.weight":    "encoder.block.{n}.layer.1.DenseReluDense.wi_1.weight",
        "ffn.fc2.weight":    "encoder.block.{n}.layer.1.DenseReluDense.wo.weight",
    }
    if rest not in tbl:
        return None
    return tbl[rest].format(n=n)

print("[load] reading source .pth (mmap) ...", flush=True)
sd = torch.load(SRC, map_location="cpu", weights_only=True, mmap=True)
print(f"[load] {len(sd)} keys, {time.time()-t0:.0f}s", flush=True)

mapped, unmapped = {}, []
for k, v in sd.items():
    hk = to_hf(k)
    if hk is None:
        unmapped.append(k)
    else:
        mapped[hk] = v
if unmapped:
    sys.exit(f"UNMAPPED SOURCE KEYS ({len(unmapped)}): {unmapped[:10]}")

missing, unexpected = [], []
if is_meta:
    missing, unexpected = model.load_state_dict(mapped, strict=False, assign=True)
else:
    missing, unexpected = model.load_state_dict(mapped, strict=False)
print(f"[check] missing={len(missing)} unexpected={len(unexpected)}", flush=True)
if unexpected:
    sys.exit(f"UNEXPECTED TARGET KEYS: {unexpected[:10]}")
if [k for k in missing if k != "encoder.embed_tokens.weight"]:
    sys.exit(f"KEY MISMATCH. missing={missing[:10]}")

# embed_tokens is the tied alias of shared.weight — re-tie it (standard for T5/UMT5)
if hasattr(model, "tie_weights"):
    model.tie_weights()
    print("[tie] tie_weights() done", flush=True)
if model.encoder.embed_tokens.weight.data_ptr() != model.shared.weight.data_ptr():
    model.encoder.embed_tokens = model.shared
    print("[tie] manually rebound encoder.embed_tokens -> shared", flush=True)
leftover = [n for n, p in model.named_parameters() if p.is_meta]
if leftover:
    sys.exit(f"META TENSORS LEFT: {leftover[:10]}")

# shape sanity on the risky one
tgt = dict(model.state_dict())
pe_src = sd["blocks.0.pos_embedding.embedding.weight"].shape
pe_tgt = tgt["encoder.block.0.layer.0.SelfAttention.relative_attention_bias.weight"].shape
print(f"[shape] pos_embedding src={tuple(pe_src)} tgt={tuple(pe_tgt)}", flush=True)
if tuple(pe_src) != tuple(pe_tgt):
    sys.exit("POS EMBEDDING SHAPE MISMATCH")

print("[save] saving text_encoder safetensors (bf16) ...", flush=True)
model.save_pretrained(TE_DIR, safe_serialization=True)
total = sum(f.stat().st_size for f in TE_DIR.iterdir() if f.is_file())
print(f"DONE in {time.time()-t0:.0f}s -> {TE_DIR} ({total/1e9:.2f} GB)", flush=True)
