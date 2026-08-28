# scripts/ghidra_c166_dump.py — Ghidra headless script (Jython)
#
# Dumps the C166 instruction stream of a raw binary to a CSV for the
# parity harness (scripts/verify_c166.py).  Also prints the memory block
# layout to a sidecar file so the harness can map file offsets -> addresses.
#
# Usage (dev machine):
#   analyzeHeadless <proj_dir> <proj_name> -import <bin> \
#       -postScript ghidra_c166_dump.py <out_prefix> -scriptPath <repo>/scripts
#
# Outputs:
#   <out_prefix>.blocks   "name,start_hex,size_hex" per memory block
#   <out_prefix>.insns    "addr_hex,len_hex,mnemonic" per disassembled insn
#
# Disassembly strategy: for every memory block, linear-disassemble from the
# block start (the raw-bin loader may page a 512KB C166 file across the
# 16-bit address space; each page is disassembled independently).
import sys

from ghidra.app.plugin.core.analysis import AutoAnalysisManager
from ghidra.program.model.listing import CodeUnit
from ghidra.util.task import ConsoleTaskMonitor

args = getScriptArgs()
out_prefix = args[0] if args else "/tmp/c166"

program = getCurrentProgram()
monitor = ConsoleTaskMonitor()
listing = program.getListing()
memory = program.getMemory()
image_base = program.getImageBase()

with open(out_prefix + ".blocks", "w") as fb:
    for block in memory.getBlocks():
        fb.write(
            "%s,%x,%x\n"
            % (block.getName(), int(block.getStart().getOffset()), int(block.getSize()))
        )

# Disassemble linearly from the start of every block (avoids control-flow
# dead ends; each block start is a valid page boundary for raw C166 code).
disassembler = listing.getDisassembler()
for block in memory.getBlocks():
    start = block.getStart()
    try:
        disassembler.disassemble(start, monitor, True)
    except Exception:
        pass

with open(out_prefix + ".insns", "w") as fi:
    insn = listing.getFirstInstruction(True)
    while insn is not None:
        addr = int(insn.getMinAddress().getOffset())
        length = int(insn.getLength())
        mnemonic = insn.getMnemonicString()
        fi.write("%x,%x,%s\n" % (addr, length, mnemonic))
        insn = listing.getInstructionAfter(insn.getMinAddress())

print("DUMPED %s blocks, %s" % (memory.getNumBlocks(), out_prefix))
