# Autonomous Educational Video Engine (AEVE)

Welcome to the **Autonomous Educational Video Engine (AEVE)**! AEVE is a fully autonomous, AI-driven pipeline that transforms a single multi-modal input (Image or Text) into a high-fidelity, synchronized mathematical animation using the Manim engine and professional-grade TTS narration.

## 🌟 Overview
AEVE is designed to be the ultimate AI tutor video generator. Instead of a single LLM attempting to write a script and code an animation at the same time (which frequently fails), AEVE uses a **10-agent LLM Orchestration Pipeline**. These agents debate, refine, and write code in distinct phases—ranging from initial mathematical problem-solving to Manim code generation and rendering error auto-recovery.

The output is a fully assembled `.mp4` video with smooth animations, beautifully rendered LaTeX equations (via Manim), and perfectly timed audio narration.

---

## 🚀 Features
- **10-Model Multi-Agent Architecture**: Dedicated AI personas for solving math, storyboarding, writing Manim code, and reviewing errors.
- **Robust Manim Auto-Fixing**: If a generated Manim script crashes during rendering, the error is caught, intelligently filtered, and sent to an AI "Code Reviewer" (M10) to automatically fix the Python code and retry.
- **Dynamic API Key Rotation**: Automatically rotates between multiple Groq API keys to elegantly handle `429 Rate Limit` errors during massive parallel generation tasks.
- **Automated Media Assembly**: Programmatically stitches together partial video segments and aligns them with TTS narration without relying on external GUI video editors.
- **Web Interface**: A sleek Flask-based frontend for monitoring pipeline progress in real-time via Server-Sent Events (SSE).

---

## 🛠️ Prerequisites
Before running AEVE, ensure you have the following installed on your machine:

1. **Anaconda / Miniconda**: Highly recommended for managing Python environments.
2. **Python 3.10+**: The pipeline requires modern Python features.
3. **FFmpeg**: Required for media concatenation.
   * *Windows Users*: AEVE works best when FFmpeg is explicitly defined in your environment path or patched directly in the conda `Library/bin` folder.
4. **Manim Community Edition (v0.19+)**: The core rendering engine for the mathematical visuals.

---

## 📥 Installation

1. **Clone the Repository**
2. **Create the Conda Environment**
   Open your terminal (Anaconda Prompt or PowerShell) and run:
   ```bash
   conda create -n cv_conda python=3.10 -y
   conda activate cv_conda
   ```
3. **Install Dependencies**
   Install Manim and other required Python packages:
   ```bash
   pip install -r requirements.txt
   ```
   *(Ensure you have PyAV and pure-python dependencies for the latest Manim version installed).*

4. **Configure API Keys**
   AEVE relies on external LLM providers (primarily Groq and OpenRouter).
   * Open `config.py`.
   * Locate the `GROQ_API_KEYS` list.
   * Add your API keys:
     ```python
     GROQ_API_KEYS = [
         "gsk_YOUR_FIRST_KEY",
         "gsk_YOUR_SECOND_KEY", # Optional: for rate limit rotation
     ]
     OPENROUTER_API_KEY = "sk-or-v1-..."
     ```
   * *Note: `config.py` is safely ignored in `.gitignore` so you don't accidentally push your secrets.*

---

## 🎮 How to Use the Engine

### 1. Start the Server
Activate your conda environment and run the main Flask application:
```bash
conda activate cv_conda
python app.py
```

### 2. Access the Web Interface
Open your web browser and navigate to:
```
http://localhost:5000
```

### 3. Generate a Video
1. On the web dashboard, enter a math problem or educational topic in the text field (e.g., *"Explain the Pythagorean Theorem"*).
2. Click **Generate Video**.
3. **Watch the live terminal feed** on the frontend UI. You will see the orchestration engine kick into gear, moving through Phase I (Knowledge Distillation), Phase II (Consensus), and Phase III (Generation & Assembly).
4. Once completed, the final video will be available in the `output/final/` directory, ready to play!

---

## 🧠 How the Process Happens (High-Level Pipeline)

When you submit a prompt, the system does not immediately start writing Python code. It follows a rigorous, multi-stage pipeline:

### Phase I: Knowledge Distillation
* **Goal**: Solve the math problem correctly.
* The system receives the prompt. The **Solver** agent works out the step-by-step mathematical solution (Script 1).
* The **Verifier** reviews the math for absolute accuracy. If a mistake is found, it is corrected before any video planning begins.

### Phase II: The Consensus Committee
* **Goal**: Plan the visual storyboard.
* The raw mathematical solution is broken down into distinct "Scenes" (e.g., Introduction, Formula Definition, Visual Proof).
* Agents add specific instruction tags for what objects should appear on screen (e.g., `Text`, `Circle`) and what the narrator should say.
* The result is a strictly formatted **Scene Manifest (JSON)** containing 3–8 scenes.

### Phase III: Generation & Assembly
* **Goal**: Write the code, record the audio, and render.
* For each scene in the manifest (processed concurrently):
  1. **TTS Engine**: Generates the `.mp3` voiceover file and calculates exactly how long the animation must last.
  2. **Manim Coder**: Writes the precise Python `manim` code for the visuals, ensuring the `self.wait()` times perfectly match the audio duration.
  3. **Manim Renderer**: Attempts to execute the Python script.
  4. **Auto-Fixing Loop**: If Manim crashes (e.g., syntax error, invalid attribute), the traceback is captured, cleaned, and sent back to the **Code Reviewer** agent. The agent writes a fixed version of the code, and Manim tries again. This loop runs up to 3 times per scene.
* Finally, the **Assembler** merges the audio with the rendered video blocks, concatenating all the scenes into one cohesive, final `.mp4` educational video.

---

## 🛠 Troubleshooting Common Issues

* **`WinError 2: FileNotFoundError` during Rendering**:
  Manim relies heavily on internal `subprocess` calls. If you see this error, ensure that FFmpeg is properly installed and accessible in the conda `PATH`. AEVE has built-in execution patches in `manim_runner.py` to auto-inject the active conda environment's library paths to resolve this.
  Additionally, this can be triggered if Manim attempts to render LaTeX (`MathTex`) but a LaTeX distribution is missing. AEVE's prompts are designed to use standard `Text` to bypass this requirement dynamically.

* **Groq Rate Limits (429 Error)**:
  AEVE makes many parallel LLM calls. The engine automatically rotates keys if provided in the `GROQ_API_KEYS` list. Ensure you have at least 2 or 3 keys if you plan to generate massive 10-scene videos simultaneously.

* **Poor Video Transitions**:
  If the visual transitions between scenes feel abrupt (e.g., objects suddenly disappearing instead of fading out), ensure your LLM model is robust enough (like Claude 3.5 Sonnet or GPT-4o) to reliably follow the strict instructional prompt to wrap all final animations in a `FadeOut` operation.

---

*Enjoy generating cutting-edge AI educational content with AEVE!*
