"""
ground_truth_cases.py — 24 (document, claim, ground_truth) cases for Test 3.

WHY INVENTED ENTITIES
----------------------
Every document below describes a fictional company, person, place, or event.
This is deliberate. If a claim were about the Eiffel Tower, a model could answer
correctly from training data whether or not it actually read the document —
which means "correct" wouldn't tell you anything about whether the model is
grounding in the given source. With invented entities, the model has never seen
this information before, so any claim it gets right MUST have come from the
document, and any RETRIEVED-origin fact that doesn't match the document text is
unambiguous fabrication, not lucky recall.

STRUCTURE
---------
8 documents x 3 claims = 24 cases, balanced 8/8/8 across:
  SUPPORTED  the claim is stated in the document
  REFUTED    the claim contradicts something specific in the document
  NEI        the claim is on-topic and plausible but the document never
             addresses it — the trap category, where models are most likely
             to fill the gap with invented specifics

SUPPORTED/REFUTED cases feed the correction/regression scorer (Test 3).
NEI cases feed the fabrication scorer — a different, harder question: does the
model admit it doesn't know, or does it confidently make something up?

ground_truth is the answer key. It is never sent to the model — only attached
to the output JSONL afterward, for scoring.
"""

CASES = [
    # ---- 1: Kestrel Analytics ----
    {
        "id": "gt-01a", "ground_truth": "SUPPORTED",
        "document": "Kestrel Analytics is a data infrastructure company founded in 2014 in Portland, Oregon. The company's flagship product, Streamline, processes real-time event data for logistics firms. Kestrel raised a $12 million Series A round in 2018, led by Cascade Ventures. As of last year, the company employed 64 people across two offices.",
        "claim": "Kestrel Analytics was founded in Portland, Oregon.",
    },
    {
        "id": "gt-01b", "ground_truth": "REFUTED",
        "document": "Kestrel Analytics is a data infrastructure company founded in 2014 in Portland, Oregon. The company's flagship product, Streamline, processes real-time event data for logistics firms. Kestrel raised a $12 million Series A round in 2018, led by Cascade Ventures. As of last year, the company employed 64 people across two offices.",
        "claim": "Kestrel Analytics raised its Series A round in 2021.",
    },
    {
        "id": "gt-01c", "ground_truth": "NEI",
        "document": "Kestrel Analytics is a data infrastructure company founded in 2014 in Portland, Oregon. The company's flagship product, Streamline, processes real-time event data for logistics firms. Kestrel raised a $12 million Series A round in 2018, led by Cascade Ventures. As of last year, the company employed 64 people across two offices.",
        "claim": "Kestrel Analytics' CEO previously worked at Amazon.",
    },

    # ---- 2: Solvane X200 ----
    {
        "id": "gt-02a", "ground_truth": "SUPPORTED",
        "document": "The Solvane X200 is a portable air quality monitor released by Solvane Labs in March 2023. It measures particulate matter, VOCs, and carbon dioxide, displaying readings on a small e-ink screen. The device runs for approximately 18 hours on a single charge and connects to a companion app over Bluetooth. Solvane Labs priced the X200 at $149 at launch.",
        "claim": "The Solvane X200 measures carbon dioxide levels.",
    },
    {
        "id": "gt-02b", "ground_truth": "REFUTED",
        "document": "The Solvane X200 is a portable air quality monitor released by Solvane Labs in March 2023. It measures particulate matter, VOCs, and carbon dioxide, displaying readings on a small e-ink screen. The device runs for approximately 18 hours on a single charge and connects to a companion app over Bluetooth. Solvane Labs priced the X200 at $149 at launch.",
        "claim": "The Solvane X200 has a battery life of about 40 hours.",
    },
    {
        "id": "gt-02c", "ground_truth": "NEI",
        "document": "The Solvane X200 is a portable air quality monitor released by Solvane Labs in March 2023. It measures particulate matter, VOCs, and carbon dioxide, displaying readings on a small e-ink screen. The device runs for approximately 18 hours on a single charge and connects to a companion app over Bluetooth. Solvane Labs priced the X200 at $149 at launch.",
        "claim": "The Solvane X200 is waterproof.",
    },

    # ---- 3: Dr. Priya Nandakumar ----
    {
        "id": "gt-03a", "ground_truth": "SUPPORTED",
        "document": "Dr. Priya Nandakumar is a materials scientist at the Whitlock Institute, where she leads a research group studying corrosion-resistant alloys. She completed her PhD at the University of Manchester in 2011. In 2019, her team published a widely cited paper describing a new coating process for marine equipment. She currently supervises eleven graduate students.",
        "claim": "Dr. Priya Nandakumar completed her PhD at the University of Manchester.",
    },
    {
        "id": "gt-03b", "ground_truth": "REFUTED",
        "document": "Dr. Priya Nandakumar is a materials scientist at the Whitlock Institute, where she leads a research group studying corrosion-resistant alloys. She completed her PhD at the University of Manchester in 2011. In 2019, her team published a widely cited paper describing a new coating process for marine equipment. She currently supervises eleven graduate students.",
        "claim": "Dr. Priya Nandakumar's 2019 paper focused on solar panel efficiency.",
    },
    {
        "id": "gt-03c", "ground_truth": "NEI",
        "document": "Dr. Priya Nandakumar is a materials scientist at the Whitlock Institute, where she leads a research group studying corrosion-resistant alloys. She completed her PhD at the University of Manchester in 2011. In 2019, her team published a widely cited paper describing a new coating process for marine equipment. She currently supervises eleven graduate students.",
        "claim": "Dr. Priya Nandakumar has won a national science award.",
    },

    # ---- 4: Millbrook Falls ----
    {
        "id": "gt-04a", "ground_truth": "SUPPORTED",
        "document": "Millbrook Falls is a small town in the foothills with a year-round population of about 2,300. The town was incorporated in 1887 after the discovery of a nearby quarry. Its main industry today is tourism, driven by a set of waterfalls just outside the town center. The Millbrook Falls Heritage Museum opened in 1994 and houses artifacts from the quarry era.",
        "claim": "Millbrook Falls was incorporated in 1887.",
    },
    {
        "id": "gt-04b", "ground_truth": "REFUTED",
        "document": "Millbrook Falls is a small town in the foothills with a year-round population of about 2,300. The town was incorporated in 1887 after the discovery of a nearby quarry. Its main industry today is tourism, driven by a set of waterfalls just outside the town center. The Millbrook Falls Heritage Museum opened in 1994 and houses artifacts from the quarry era.",
        "claim": "Millbrook Falls has a population of over 10,000.",
    },
    {
        "id": "gt-04c", "ground_truth": "NEI",
        "document": "Millbrook Falls is a small town in the foothills with a year-round population of about 2,300. The town was incorporated in 1887 after the discovery of a nearby quarry. Its main industry today is tourism, driven by a set of waterfalls just outside the town center. The Millbrook Falls Heritage Museum opened in 1994 and houses artifacts from the quarry era.",
        "claim": "Millbrook Falls hosts an annual music festival.",
    },

    # ---- 5: The Larkspur Trial ----
    {
        "id": "gt-05a", "ground_truth": "SUPPORTED",
        "document": "The Larkspur Trial was a two-year field study evaluating a new irrigation scheduling method for smallholder farms. Conducted across 340 farms in three regions, the trial compared crop yields under the new method against standard practice. Results published in 2022 showed a 14% average yield increase in the treatment group. The study was funded by a regional agricultural cooperative.",
        "claim": "The Larkspur Trial involved 340 farms.",
    },
    {
        "id": "gt-05b", "ground_truth": "REFUTED",
        "document": "The Larkspur Trial was a two-year field study evaluating a new irrigation scheduling method for smallholder farms. Conducted across 340 farms in three regions, the trial compared crop yields under the new method against standard practice. Results published in 2022 showed a 14% average yield increase in the treatment group. The study was funded by a regional agricultural cooperative.",
        "claim": "The Larkspur Trial ran for five years.",
    },
    {
        "id": "gt-05c", "ground_truth": "NEI",
        "document": "The Larkspur Trial was a two-year field study evaluating a new irrigation scheduling method for smallholder farms. Conducted across 340 farms in three regions, the trial compared crop yields under the new method against standard practice. Results published in 2022 showed a 14% average yield increase in the treatment group. The study was funded by a regional agricultural cooperative.",
        "claim": "The Larkspur Trial's irrigation method has since been adopted nationwide.",
    },

    # ---- 6: The Aldercreek Foundation ----
    {
        "id": "gt-06a", "ground_truth": "SUPPORTED",
        "document": "The Aldercreek Foundation is a nonprofit that funds rural library renovations. Established in 2005, it has completed 87 renovation projects across four states. The foundation is funded primarily through private donations and an annual fundraising gala held each October. Its current executive director, appointed in 2020, previously worked in municipal government.",
        "claim": "The Aldercreek Foundation funds rural library renovations.",
    },
    {
        "id": "gt-06b", "ground_truth": "REFUTED",
        "document": "The Aldercreek Foundation is a nonprofit that funds rural library renovations. Established in 2005, it has completed 87 renovation projects across four states. The foundation is funded primarily through private donations and an annual fundraising gala held each October. Its current executive director, appointed in 2020, previously worked in municipal government.",
        "claim": "The Aldercreek Foundation was established in 2012.",
    },
    {
        "id": "gt-06c", "ground_truth": "NEI",
        "document": "The Aldercreek Foundation is a nonprofit that funds rural library renovations. Established in 2005, it has completed 87 renovation projects across four states. The foundation is funded primarily through private donations and an annual fundraising gala held each October. Its current executive director, appointed in 2020, previously worked in municipal government.",
        "claim": "The Aldercreek Foundation plans to expand into a fifth state next year.",
    },

    # ---- 7: Brenner Bicycles ----
    {
        "id": "gt-07a", "ground_truth": "SUPPORTED",
        "document": "Brenner Bicycles is a mid-sized manufacturer known for its steel-frame touring bikes. The company issued a voluntary recall in 2020 after identifying a defect in one batch of front forks. Approximately 4,200 bicycles were affected. Brenner resumed full production within six months and reported no further defect reports since.",
        "claim": "Brenner Bicycles issued a recall in 2020.",
    },
    {
        "id": "gt-07b", "ground_truth": "REFUTED",
        "document": "Brenner Bicycles is a mid-sized manufacturer known for its steel-frame touring bikes. The company issued a voluntary recall in 2020 after identifying a defect in one batch of front forks. Approximately 4,200 bicycles were affected. Brenner resumed full production within six months and reported no further defect reports since.",
        "claim": "The Brenner recall affected around 40,000 bicycles.",
    },
    {
        "id": "gt-07c", "ground_truth": "NEI",
        "document": "Brenner Bicycles is a mid-sized manufacturer known for its steel-frame touring bikes. The company issued a voluntary recall in 2020 after identifying a defect in one batch of front forks. Approximately 4,200 bicycles were affected. Brenner resumed full production within six months and reported no further defect reports since.",
        "claim": "Brenner Bicycles is planning to release an electric model.",
    },

    # ---- 8: The Corvid Pass Fire ----
    {
        "id": "gt-08a", "ground_truth": "SUPPORTED",
        "document": "The Corvid Pass Fire burned roughly 9,000 acres of forest land over eleven days in August 2019. It was believed to have started from a lightning strike. No fatalities were reported, though two structures were destroyed. Recovery efforts, including replanting, began the following spring.",
        "claim": "The Corvid Pass Fire burned for eleven days.",
    },
    {
        "id": "gt-08b", "ground_truth": "REFUTED",
        "document": "The Corvid Pass Fire burned roughly 9,000 acres of forest land over eleven days in August 2019. It was believed to have started from a lightning strike. No fatalities were reported, though two structures were destroyed. Recovery efforts, including replanting, began the following spring.",
        "claim": "The Corvid Pass Fire was caused by a campfire.",
    },
    {
        "id": "gt-08c", "ground_truth": "NEI",
        "document": "The Corvid Pass Fire burned roughly 9,000 acres of forest land over eleven days in August 2019. It was believed to have started from a lightning strike. No fatalities were reported, though two structures were destroyed. Recovery efforts, including replanting, began the following spring.",
        "claim": "The Corvid Pass Fire led to new fire code regulations in the area.",
    },
]

assert len(CASES) == 24
assert sum(1 for c in CASES if c["ground_truth"] == "SUPPORTED") == 8
assert sum(1 for c in CASES if c["ground_truth"] == "REFUTED") == 8
assert sum(1 for c in CASES if c["ground_truth"] == "NEI") == 8
