VERIFIED ✓
## Definitions
To approach this problem, we first need to understand the given statement and what it's asking us to prove. The statement involves a limit, specifically:
\[ \lim_{x \to a} f(x) = L \]
This means that as $x$ approaches $a$, the function $f(x)$ approaches the value $L$. In our case, we're given:
\[ \lim_{x \to 2} \frac{x^2 - 4}{x - 2} = \lim_{x \to 2} (x + 2) \]
We need to define what a limit is and how it applies to the given function. The limit of a function $f(x)$ as $x$ approaches $a$ is denoted by $\lim_{x \to a} f(x)$ and represents the value that $f(x)$ approaches as $x$ gets arbitrarily close to $a$.

## Solution
To prove that $\lim_{x \to 2} \frac{x^2 - 4}{x - 2} = \lim_{x \to 2} (x + 2)$, we'll follow these steps:

### Step 1: Factor the Numerator
First, let's factor the numerator of the fraction $\frac{x^2 - 4}{x - 2}$. The numerator can be factored as a difference of squares:
\[ x^2 - 4 = (x + 2)(x - 2) \]
So, the expression becomes:
\[ \frac{(x + 2)(x - 2)}{x - 2} \]

### Step 2: Cancel Common Factors
Now, we notice that $(x - 2)$ is present in both the numerator and the denominator. As long as $x \neq 2$, we can cancel these factors:
\[ \frac{(x + 2)(x - 2)}{x - 2} = x + 2 \]
This simplification is valid for all $x \neq 2$, which is acceptable when dealing with limits because the limit as $x$ approaches $2$ does not depend on the value of the function at $x = 2$ itself.

### Step 3: Evaluate the Limit
Given the simplified expression $x + 2$, we can now evaluate the limit as $x$ approaches $2$:
\[ \lim_{x \to 2} (x + 2) \]
Substituting $x = 2$ into the expression $x + 2$ gives us:
\[ \lim_{x \to 2} (x + 2) = 2 + 2 = 4 \]
To understand why we can directly substitute $x = 2$ into $x + 2$ to find the limit, recall that the function $f(x) = x + 2$ is continuous at $x = 2$. For continuous functions, the limit as $x$ approaches $a$ is equal to $f(a)$.

### Step 4: Conclusion of the Limit Evaluation
From Step 3, we see that as $x$ approaches $2$, the value of $x + 2$ approaches $4$. This means:
\[ \lim_{x \to 2} \frac{x^2 - 4}{x - 2} = \lim_{x \to 2} (x + 2) = 4 \]
Therefore, we have shown that the original limit statement is true, with both sides of the equation approaching the value $4$ as $x$ approaches $2$.

## Conclusion
In conclusion, we have shown that $\lim_{x \to 2} \frac{x^2 - 4}{x - 2} = \lim_{x \to 2} (x + 2)$ by factoring the numerator, canceling common factors, and then evaluating the limit of the simplified expression. This demonstrates that the original limit statement is true, with both sides of the equation approaching the value $4$ as $x$ approaches $2$. Therefore, we have successfully proven the given statement using basic principles of algebra and limit evaluation.