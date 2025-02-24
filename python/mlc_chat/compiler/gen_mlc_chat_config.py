"""Generator of mlc-chat-config.json and tokenizer configuration."""
import dataclasses
import json
import logging
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..support.style import bold, green, red
from .flags_model_config_override import ModelConfigOverride
from .model import Model
from .quantization import Quantization

logger = logging.getLogger(__name__)

FOUND = green("Found")
NOT_FOUND = red("Not found")
VERSION = "0.1.0"


@dataclasses.dataclass
class MLCChatConfig:  # pylint: disable=too-many-instance-attributes
    """Arguments for `mlc_chat.compiler.gen_config`."""

    version: str = VERSION

    model_type: str = None
    quantization: str = None
    model_config: Dict[str, Any] = None
    vocab_size: int = None
    max_window_size: int = None

    temperature: float = None
    repetition_penalty: float = None
    top_p: float = None

    mean_gen_len: int = None
    max_gen_len: int = None
    shift_fill_factor: float = None
    sliding_window: int = None
    prefill_chunk_size: int = None

    # Conversation template
    conv_template: str = None
    pad_token_id: int = None
    bos_token_id: int = None
    eos_token_id: int = None
    tokenizer_files: List[str] = dataclasses.field(default_factory=list)


def gen_config(  # pylint: disable=too-many-locals,too-many-arguments,too-many-branches,too-many-statements
    config: Path,
    model: Model,
    quantization: Quantization,
    conv_template: str,
    context_window_size: Optional[int],
    sliding_window: Optional[int],
    prefill_chunk_size: Optional[int],
    output: Path,
):
    """Entrypoint of MLC Chat configuration generation."""
    with config.open("r", encoding="utf-8") as in_file:
        model_config_json = json.load(in_file)
    model_config = model.config.from_dict(model_config_json)
    ModelConfigOverride(
        context_window_size=context_window_size,
        sliding_window=sliding_window,
        prefill_chunk_size=prefill_chunk_size,
    ).apply(model_config)

    mlc_chat_config = MLCChatConfig(
        model_type=model.name,
        quantization=quantization.name,
        model_config=model_config_json,
        vocab_size=model_config.vocab_size,
        conv_template=conv_template,
        max_window_size=model_config.context_window_size,
    )
    # Step 1. Load `config.json`
    for key, value in model_config.__dict__.items():
        if hasattr(mlc_chat_config, key) and getattr(mlc_chat_config, key) is None:
            setattr(mlc_chat_config, key, value)
            logger.info("[config.json] Setting %s: %s", bold(key), value)
    # Step 2. Load `generation_config.json`
    generation_config = config.parent / "generation_config.json"
    if generation_config.exists():
        logger.info("%s generation_config.json: %s", FOUND, generation_config)
        with generation_config.open("r", encoding="utf-8") as in_file:
            generation_config_json = json.load(in_file)
        for key, value in generation_config_json.items():
            if hasattr(mlc_chat_config, key) and getattr(mlc_chat_config, key) is None:
                setattr(mlc_chat_config, key, value)
                logger.info("[generation_config.json] Setting %s: %s", bold(key), value)
    else:
        logger.info("%s generation_config.json: %s", NOT_FOUND, generation_config)
    # Step 3. Copy tokenizer configuration
    # 3.1. Copy over the files and populate mlc_chat_config
    for filename in TOKENIZER_FILES:
        file = config.parent / filename
        if file.exists():
            mlc_chat_config.tokenizer_files.append(filename)
            dest = output / filename
            shutil.copy(file, dest)
            logger.info("%s tokenizer config: %s. Copying to %s", FOUND, file, bold(str(dest)))
        else:
            logger.info("%s tokenizer config: %s", NOT_FOUND, file)
    # 3.2. If we have `tokenizer.model` but not `tokenizer.json`, try convert it to
    # `tokenizer.json` with `transformers`.
    tokenizer_json_file = config.parent / "tokenizer.json"
    tokenizer_model_file = config.parent / "tokenizer.model"
    if tokenizer_model_file.exists() and (not tokenizer_json_file.exists()):
        logger.info("Attempting to convert `tokenizer.model` to `tokenizer.json`.")
        try:
            # pylint: disable=import-outside-toplevel
            from transformers import AutoTokenizer

            tokenizer_json_save_dest = output / "tokenizer.json"
            fast_tokenizer = AutoTokenizer.from_pretrained(str(config.parent), use_fast=True)
            fast_tokenizer.backend_tokenizer.save(str(tokenizer_json_save_dest))
            mlc_chat_config.tokenizer_files.append("tokenizer.json")
            logger.info("Succesfully converted `tokenizer.model` to: %s", tokenizer_json_save_dest)
        except ImportError:
            logger.warning(
                "The model has `tokenizer.model` but not `tokenizer.json`. It is"
                + "recommended to use `tokenizer.json`, so we try convert it with `transformers`.\n"
                + "However, we were unable to import `transformers`, hence skipping this step."
            )
        except Exception as error:  # pylint: disable=broad-exception-caught
            logger.warning(
                "The model has `tokenizer.model` but not `tokenizer.json`. It is"
                + "recommended to use `tokenizer.json`, so we try convert it with `transformers`.\n"
                + "However, we are skipping this due to an error:\n",
                error,
            )
    # Step 4. Load system default value
    for key, value in DEFAULT_CONFIGS.items():
        if getattr(mlc_chat_config, key) is None:
            setattr(mlc_chat_config, key, value)
            logger.info("[System default] Setting %s: %s", bold(key), value)

    mlc_chat_config_dict = dataclasses.asdict(mlc_chat_config)
    if mlc_chat_config_dict["sliding_window"] is not None:
        del mlc_chat_config_dict["max_window_size"]
        logger.info("[CleanUp] Deleting %s", bold("max_window_size"))
    for key, value in list(mlc_chat_config_dict.items()):
        if value is None:
            del mlc_chat_config_dict[key]
            logger.info("[CleanUp] Deleting %s", bold(key))

    # Dump the configuration file to output directory
    out = output / "mlc-chat-config.json"
    with out.open("w", encoding="utf-8") as out_file:
        json.dump(mlc_chat_config_dict, out_file, indent=2)
    logger.info("Dumping configuration file to: %s", bold(str(out)))


DEFAULT_CONFIGS = {
    # Conversation
    "pad_token_id": 0,
    "bos_token_id": 1,
    "eos_token_id": 2,
    # Configuration of text generation
    "temperature": 0.7,
    "repetition_penalty": 1.0,
    "top_p": 0.95,
    # Control the behavior of the runtime
    "mean_gen_len": 128,
    "max_gen_len": 512,
    "shift_fill_factor": 0.3,
}

TOKENIZER_FILES = [
    "tokenizer.model",
    "tokenizer.json",
    "vocab.json",
    "merges.txt",
    "added_tokens.json",
    "tokenizer_config.json",
]

CONV_TEMPLATES = {
    "chatml",
    "open_hermes_mistral",
    "llama_default",
    "llama-2",
    "mistral_default",
    "gpt2",
    "codellama_completion",
    "codellama_instruct",
    "vicuna_v1.1",
    "conv_one_shot",
    "redpajama_chat",
    "rwkv_world",
    "rwkv",
    "gorilla",
    "guanaco",
    "dolly",
    "oasst",
    "stablelm",
    "stablecode_completion",
    "stablecode_instruct",
    "minigpt",
    "moss",
    "LM",
    "stablelm-3b",
    "gpt_bigcode",
    "wizardlm_7b",
    "wizard_coder_or_math",
    "glm",
}
