import type { VisualizerConfig } from "../visualizers/types.ts";
import { ownConfig } from "../visualizers/config.ts";

export interface CodeTraceExample {
  readonly id: string;
  readonly title: string;
  readonly sourceCode: string;
  readonly visualizerConfig: VisualizerConfig;
}
const global = (name: string) => ({ scope: "globals" as const, name });
function example(
  id: string,
  title: string,
  sourceCode: string,
  visualizerConfig: VisualizerConfig,
): CodeTraceExample {
  return Object.freeze({
    id,
    title,
    sourceCode,
    visualizerConfig: ownConfig(visualizerConfig),
  });
}
/** Source and presentation only. Every Run uses the ordinary Runner. */
export const EXAMPLES: readonly CodeTraceExample[] = Object.freeze([
  example(
    "loop",
    "Цикл",
    `total = 0

for i in range(1, 6):
    total += i

print("Сумма:", total)
`,
    { kind: "none" },
  ),
  example(
    "list-mutation",
    "Изменение списка",
    `numbers = [4, 1, 7, 2]
numbers[1] = 9
numbers.append(5)
numbers.pop(0)

print(numbers)
`,
    { kind: "array", array: global("numbers"), mode: "cells", indices: [] },
  ),
  example(
    "sorting",
    "Сортировка",
    `numbers = [5, 1, 4, 2, 8]
n = len(numbers)

for i in range(n):
    min_index = i
    for j in range(i + 1, n):
        if numbers[j] < numbers[min_index]:
            min_index = j

    numbers[i], numbers[min_index] = numbers[min_index], numbers[i]

print(numbers)
`,
    {
      kind: "array",
      array: global("numbers"),
      mode: "bars",
      indices: [global("i"), global("j")],
    },
  ),
  example(
    "function",
    "Функция",
    `def rectangle_area(width, height):
    area = width * height
    return area

result = rectangle_area(6, 4)
print(result)
`,
    { kind: "call_stack" },
  ),
  example(
    "recursive-factorial",
    "Рекурсивный факториал",
    `def factorial(n):
    if n <= 1:
        return 1

    return n * factorial(n - 1)

result = factorial(5)
print(result)
`,
    { kind: "call_stack" },
  ),
  example(
    "binary-tree-dfs",
    "DFS бинарного дерева",
    `class Node:
    def __init__(self, value, left=None, right=None):
        self.value = value
        self.left = left
        self.right = right

root = Node(
    1,
    Node(2, Node(4), Node(5)),
    Node(3),
)

visited = []
current = None
stack = [root]

while stack:
    current = stack.pop()
    visited.append(current)

    if current.right is not None:
        stack.append(current.right)

    if current.left is not None:
        stack.append(current.left)

print("Посещено:", len(visited))
`,
    {
      kind: "tree",
      root: global("root"),
      valueField: "value",
      leftField: "left",
      rightField: "right",
      current: global("current"),
      visited: global("visited"),
    },
  ),
  example(
    "graph-bfs",
    "BFS графа",
    `from collections import deque

graph = {
    1: [2, 3],
    2: [4],
    3: [4],
    4: [],
}

visited = []
queued = {1}
queue = deque([1])
current = None

while queue:
    current = queue.popleft()
    visited.append(current)

    for neighbor in graph[current]:
        if neighbor not in queued:
            queued.add(neighbor)
            queue.append(neighbor)

print(visited)
`,
    {
      kind: "graph",
      adjacency: global("graph"),
      current: global("current"),
      visited: global("visited"),
      queue: global("queue"),
    },
  ),
]);
