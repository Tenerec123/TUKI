# Contributing to T.U.K.I. TODO Project

Thank you for your interest in contributing! Here's how you can help.

## Code of Conduct

Be respectful and constructive in all interactions.

## Getting Started

1. Fork the repository
2. Clone your fork: `git clone <your-fork>`
3. Create a feature branch: `git checkout -b feature/amazing-feature`
4. Make your changes
5. Commit with clear messages: `git commit -m 'Add amazing feature'`
6. Push to your branch: `git push origin feature/amazing-feature`
7. Open a Pull Request

## Development Setup

```bash
# Create virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Setup .env file
cp .env.example .env
# Edit .env with your API keys
```

## Running the Application

```bash
# From the project root directory
python -m uvicorn backend.main:api --reload --host 0.0.0.0
```

## Code Style

- Use 4 spaces for indentation
- Follow PEP 8 guidelines
- Add type hints to functions
- Write docstrings for classes and functions

## Submitting Changes

1. Ensure your code follows the style guidelines
2. Test your changes locally
3. Update documentation if needed
4. Reference any related issues in your PR description

## Reporting Bugs

Include:
- Clear description of the bug
- Steps to reproduce
- Expected vs actual behavior
- Python version and OS
- Error logs/tracebacks

## Questions?

Open an issue with the `question` label.

Thank you for contributing! 🚀
