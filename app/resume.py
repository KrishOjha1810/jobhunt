"""Parse a resume (PDF or DOCX) into text and extract skill/role keywords."""
import re
from pathlib import Path

# Common tech skills / role terms we look for. Extend freely.
SKILL_VOCAB = [
    # languages
    "python", "javascript", "typescript", "java", "rust", "solidity", "go", "golang", "c++",
    "kotlin", "scala", "ruby", "php", "sql",
    # backend / frameworks
    "node.js", "nodejs", "node", "nestjs", "express", "django", "flask", "fastapi", "spring",
    "axum", "tokio", "tower", "graphql", "rest", "microservices", "grpc",
    # frontend
    "react", "next.js", "nextjs", "redux", "vue", "angular", "tailwind", "html", "css",
    # data / infra
    "postgresql", "postgres", "mysql", "mongodb", "redis", "docker", "kubernetes", "aws",
    "gcp", "azure", "kafka", "terraform", "ci/cd", "git",
    # web3 / blockchain
    "blockchain", "smart contract", "smart contracts", "ethereum", "evm", "defi", "web3",
    "foundry", "hardhat", "ethers", "web3.js", "layerzero", "uniswap", "cross-chain", "bridge",
    "permit2", "erc-4337", "erc20", "erc721", "mev", "rpc",
    # roles / domains
    "backend", "frontend", "full-stack", "fullstack", "full stack", "devops", "data engineer",
    "machine learning", "ml", "ai", "protocol engineer", "security", "qa", "sdet", "automation",
    "distributed systems", "api",
]

SENIORITY = ["intern", "junior", "associate", "mid", "senior", "lead", "staff", "principal"]


def extract_text(path: str) -> str:
    p = Path(path)
    suffix = p.suffix.lower()
    if suffix == ".pdf":
        from pypdf import PdfReader
        reader = PdfReader(path)
        return "\n".join((page.extract_text() or "") for page in reader.pages)
    if suffix in (".docx", ".doc"):
        import docx
        d = docx.Document(path)
        return "\n".join(par.text for par in d.paragraphs)
    if suffix in (".txt", ".md"):
        return p.read_text(errors="ignore")
    raise ValueError(f"Unsupported resume format: {suffix}")


def extract_keywords(text: str) -> list:
    """Return the skill/role keywords present in the resume text."""
    low = text.lower()
    found = []
    for skill in SKILL_VOCAB:
        # word-ish boundary match so "go" doesn't match "google"
        pattern = r"(?<![a-z0-9])" + re.escape(skill) + r"(?![a-z0-9])"
        if re.search(pattern, low):
            found.append(skill)
    # dedup while preserving order
    seen = set()
    out = []
    for k in found:
        if k not in seen:
            seen.add(k)
            out.append(k)
    return out


def profile_from_resume(path: str) -> dict:
    text = extract_text(path)
    keywords = extract_keywords(text)
    seniority = [s for s in SENIORITY if re.search(r"(?<![a-z])" + s + r"(?![a-z])", text.lower())]
    return {"keywords": keywords, "seniority": seniority, "text": text, "text_len": len(text)}
