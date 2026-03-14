# LLM Orchestration Architecture in AEVE

The Autonomous Educational Video Engine (AEVE) is powered by a sophisticated **10-Model Multi-Agent Orchestration Pipeline**. This document explains the exact lifecycle of a prompt as it moves through the AI engine, from raw input to fully synthesized instructional video.

## 🏗️ The 10-Agent Pipeline Overview
Creating an educational video requires multiple distinct skill sets: mathematical problem-solving, pedagogical storyboarding, Python coding (specifically Manim Community Edition), code debugging, and text-to-speech synthesis. 

AEVE separates these concerns into specialized "AI Agents" (M1 through M10). This prevents a single LLM from hallucinating instructions or confusing the visual layout with the mathematical logic.

---

## Phase I: Knowledge Distillation
*The goal of Phase I is solely to solve the problem and verify its accuracy before any video planning begins.*

### **M1: The Solver**
* **Role**: The domain expert.
* **Function**: Receives the raw user input (text or image OCR) and writes a "Deep Solution" document. This is a comprehensive, step-by-step mathematical explanation. It does not think about visuals, scenes, or Manim at all. It focuses 100% on getting the math right.

### **M2: The Verifier**
* **Role**: The peer reviewer.
* **Function**: Takes M1's Deep Solution and mathematically audits it. If M1 made a calculation error, missed a step, or provided a confusing explanation, M2 returns a critique. The pipeline will reject the solution until M2 signs off with a perfect validation score.
* *Result*: A perfectly accurate "Script 1" mathematical solution.

---

## Phase II: The Consensus Committee
*The goal of Phase II is to take the verified math and break it down into a visual storyboard.*

### **M3: The Storyboarder**
* **Role**: The director.
* **Function**: Reads Script 1 and chunks it into 3 to 8 logical **Scenes**. For each scene, M3 writes a conceptual title, the core idea being taught, what the narrator should say, and a high-level visual description of what the viewer should see on screen. It outputs a structured JSON array.

### **M4: The Visual Detailer**
* **Role**: The Manim expert planner.
* **Function**: Takes M3's storyboard JSON and injects strict `manim_logic` instructions into every scene. It specifies exactly which Manim objects to use (e.g., *“Create a Text object for the equation, use a FadeIn animation, position it `next_to` the title...”*). It acts as a bridge between human descriptions and Python graphics.

### **M5: The Technical Critic**
* **Role**: The feasibility checker.
* **Function**: Reviews M4’s `manim_logic` to ensure no impossible or deprecated commands were suggested. For example, if M4 suggested a complex 3D shader that would crash the engine, M5 intercepts it and rewrites the instruction to use simpler, reliable 2D objects.

### **M6: The Finalizer**
* **Role**: The JSON strict compiler.
* **Function**: Takes the reviewed scenes and normalizes the data structure. It ensures all keys exist, cleans up escaped characters, and produces the final **Scene Manifest**. This manifest is the absolute blueprint for the rest of the generation process.

---

## Phase III: Generation, Execution, & Verification
*This phase runs in massive parallel blocks. Every scene in the manifest is processed simultaneously to save time.*

### **M7: Voice-Over Polisher**
* **Role**: The narrator script optimizer.
* **Function**: Takes the raw narration text from the Scene Manifest and polishes it for text-to-speech synthesis (e.g., expanding abbreviations, adding phonetic pauses, ensuring conversational flow).

### **M8: Audio Synthesizer (TTS)**
* **Role**: The edge-TTS engine.
* **Function**: Converts M7's script into a high-quality `.mp3` audio file. 
* **Crucial Step**: M8 calculates the exact duration of the generated audio (e.g., `12.4 seconds`) and passes this timestamp forward to the coder, ensuring the animations never end before the narrator finishes speaking.

### **M9: The Manim Coder**
* **Role**: The Python developer.
* **Function**: Receives the `manim_logic` instructions, the polished narration text, and the strict `audio_duration` target. M9 writes the actual, executable Python script (`scene_XXX.py`). It is explicitly prompted to end the scene with `self.wait(audio_duration)` so the visual timing flawlessly matches the audio track.

### **M10: The Code Reviewer (Auto-Fixer Unit)**
* **Role**: The debugger.
* **Function**: AEVE attempts to render M9's Python script using the local Manim engine. 
  * If the render succeeds, M10 is bypassed.
  * If the render **crashes** (due to syntax errors, invalid Manim APIs, or bad object positioning), AEVE extracts the exact traceback error from the terminal output. It feeds the broken source code *and* the traceback error directly to M10.
  * M10 reads the error, diagnoses the bug, rewrites the Python code to fix it, and passes it back to the renderer. 
  * *This self-healing loop can retry up to 3 times per scene.*

---

## Advanced Mechanism Highlights

### 1. Dynamic API Key Rotation (Handling Groq 429 Limit)
Because Phase III expands into 8 simultaneous threads (one for each scene), and each thread makes rapidly consecutive requests (M7 -> M9), aggressive LLM endpoints like Groq will instantly throw `429 Too Many Requests` rate limit errors.
AEVE intercepts any 429 response *before* crashing the application, instantly rotates the global connection handler to the next available API key in `config.py`, logs the switch (`Rotated to Groq key 2/3`), and immediately retries the failed request on the new key.

### 2. Auto-Sanitization Filter
Before M9's code ever hits the Manim renderer, AEVE runs it through a Python-based regex `sanitize_manim_code()` filter. This aggressively cleans up known LLM hallucinations—such as trying to instantiate an object using `MathTex` when the host system lacks a LaTeX distribution, or formatting `Polygon([list])` incorrectly—by hot-swapping the bad code strings with correct `Text()` and `Polygon(*args)` syntax.

### 3. Asynchronous Model Dispatch
The system is built on asynchronous coroutines. While Scene 1 is stuck in an M10 debugging loop, Scenes 2, 3, and 4 are already being rendered, encoded, and prepared for final assembly.

---

AEVE's orchestration ensures that an extremely fragile process—writing code that visually animates mathematical concepts while synced to an audio track—is executed with high reliability and zero human intervention.
