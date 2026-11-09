"""Create and upload a two-column, 100-question word-definition assessment dataset."""
import csv
import hashlib
import io
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlalchemy import select
from pipeline.db import sessions
from pipeline.intake import IntakeService
from pipeline.models import ClientTaskRevision
from pipeline.csv_uploads import TaskUploadBatch, save_csv_batch
from pipeline.assessment_agent import AssessmentJob, run_agent

ROOT = Path(__file__).resolve().parents[1]
ITEMS = '''thermometer|An instrument used to measure temperature.
compass|An instrument with a magnetic needle that indicates north for navigation.
microscope|An instrument that magnifies objects too small to see clearly with the unaided eye.
telescope|An optical instrument used to observe distant stars and planets.
barometer|An instrument used to measure atmospheric pressure.
seismograph|An instrument that detects and records earthquake vibrations.
stopwatch|A handheld timer used to measure the elapsed time of an event.
calendar|A chart that organizes the days into weeks, months, and years.
dictionary|A reference book that lists words and explains their meanings.
atlas|A bound collection of geographical maps.
biography|A written account of a person's life written by someone else.
autobiography|A written account of a person's life written by that person.
encyclopedia|A reference work containing articles about many branches of knowledge.
library|A place where books and other reading materials are kept for people to borrow or consult.
museum|An institution that collects and displays objects of historical, scientific, or artistic interest.
aquarium|A transparent tank in which fish and other aquatic animals are kept.
planetarium|A building with a domed ceiling onto which images of stars and planets are projected.
observatory|A facility equipped with telescopes for studying celestial objects.
laboratory|A room equipped for carrying out scientific experiments.
greenhouse|A structure with transparent walls and a roof used to grow plants in a controlled environment.
photosynthesis|The process by which green plants use sunlight to make food from water and carbon dioxide.
evaporation|The process by which a liquid changes into a gas at its surface.
condensation|The change of a substance from a gas into a liquid.
freezing|The change of a liquid into a solid when its temperature falls sufficiently.
melting|The change of a solid into a liquid when it is heated sufficiently.
sublimation|The direct change of a solid into a gas without passing through a liquid state.
gravity|The attractive force between objects that have mass.
friction|The force that resists motion between surfaces touching each other.
inertia|The tendency of an object to resist a change in its state of motion.
buoyancy|The upward force exerted by a fluid on an object immersed in it.
magnetism|The physical phenomenon responsible for the attraction of iron by a magnet.
refraction|The bending of light as it passes between materials with different optical properties.
reflection|The bouncing back of light from a surface.
diffusion|The net spreading of particles from a region of higher concentration to one of lower concentration.
osmosis|The movement of water through a selectively permeable membrane toward a region with a higher solute concentration.
erosion|The wearing away and transport of soil or rock by water, wind, or ice.
sediment|Solid particles that settle at the bottom of a liquid or are deposited by wind or water.
fossil|A preserved remain, impression, or trace of an organism from the distant past.
volcano|An opening in Earth's crust through which molten rock, ash, and gases can escape.
glacier|A large mass of land ice that moves slowly under its own weight.
island|An area of land completely surrounded by water.
peninsula|A piece of land almost surrounded by water but connected to a larger landmass.
archipelago|A group or chain of islands.
isthmus|A narrow strip of land joining two larger land areas with water on both sides.
delta|A sediment deposit formed where a river enters a sea or lake and often divides into branches.
estuary|The tidal mouth of a river where fresh water mixes with seawater.
tributary|A smaller river or stream that flows into a larger river.
waterfall|A place where a river or stream drops steeply over a rock ledge.
oasis|A fertile place with water in an otherwise dry desert.
plateau|A broad area of elevated land with a relatively flat top.
canyon|A deep, narrow valley with steep sides, often carved by a river.
valley|An elongated low area of land between hills or mountains.
summit|The highest point of a mountain.
equator|The imaginary line around Earth halfway between the North and South Poles.
hemisphere|One half of a sphere, especially one half of Earth.
latitude|Angular distance north or south of Earth's equator.
longitude|Angular distance east or west of the prime meridian.
orbit|The curved path followed by an object moving around a planet, star, or other body.
satellite|A natural or artificial object that travels around a larger body in space.
asteroid|A relatively small rocky body that orbits the Sun, especially in the belt between Mars and Jupiter.
comet|An icy body orbiting the Sun that can develop a glowing head and tail near the Sun.
galaxy|A vast system of stars, gas, and dust held together by gravity.
constellation|A named pattern of stars as seen from Earth.
eclipse|An event in which one celestial body obscures another or passes into its shadow.
tide|The regular rise and fall of sea level caused mainly by the gravitational attraction of the Moon and Sun.
habitat|The natural place or environment in which an organism lives.
ecosystem|A community of organisms together with the nonliving environment with which they interact.
species|A biological group whose members can ordinarily interbreed and produce fertile offspring.
predator|An animal that hunts and eats other animals.
prey|An animal hunted and eaten by another animal.
herbivore|An animal that feeds on plants.
carnivore|An animal that feeds on other animals.
omnivore|An animal that eats both plant and animal food.
nocturnal|Active mainly during the night rather than the day.
migration|The seasonal movement of animals from one region to another.
hibernation|A prolonged state of greatly reduced activity and metabolism that helps some animals survive winter.
camouflage|Coloration or a disguise that makes an organism or object blend with its surroundings.
pollination|The transfer of pollen to the receptive part of a flower, enabling fertilization.
germination|The beginning of growth of a plant from a seed.
chlorophyll|The green pigment in plants that absorbs light for photosynthesis.
root|The part of a plant that usually grows underground and absorbs water and minerals.
stem|The main supporting stalk of a plant that carries leaves and transports water and nutrients.
petal|One of the often brightly colored parts surrounding the reproductive structures of a flower.
stamen|The pollen-producing male reproductive structure of a flower.
vertebrate|An animal that has a backbone.
invertebrate|An animal that does not have a backbone.
mammal|A warm-blooded vertebrate whose females produce milk to feed their young.
amphibian|A vertebrate such as a frog that typically has an aquatic larval stage and a land-dwelling adult stage.
reptile|An air-breathing vertebrate with scaly skin, such as a snake or lizard.
arachnid|An eight-legged arthropod such as a spider or scorpion.
insect|An arthropod with six legs and three main body sections: head, thorax, and abdomen.
crustacean|An arthropod such as a crab or lobster that usually has a hard outer shell and two pairs of antennae.
metamorphosis|A major change in an animal's body form during development, such as a caterpillar becoming a butterfly.
antonym|A word with a meaning opposite to that of another word.
synonym|A word with the same or nearly the same meaning as another word.
homophone|A word pronounced the same as another word but with a different meaning or spelling.
metaphor|A figure of speech that describes one thing as another to suggest a resemblance.
simile|A figure of speech that explicitly compares things using words such as like or as.
alliteration|The repetition of the same initial consonant sound in nearby words.
onomatopoeia|The formation or use of a word that imitates a sound, such as buzz or hiss.'''


def main():
    pairs = [line.split('|', 1) for line in ITEMS.splitlines()]
    assert len(pairs) == 100 and len({d for _, d in pairs}) == 100
    rows = [dict(definition=definition, answer=word if i % 4 else pairs[(i + 13) % 100][0])
            for i, (word, definition) in enumerate(pairs)]
    directory = ROOT / 'artifacts/definition_quiz'
    directory.mkdir(parents=True, exist_ok=True)
    output = io.StringIO(newline='')
    writer = csv.DictWriter(output, fieldnames=['definition', 'answer'])
    writer.writeheader(); writer.writerows(rows)
    raw = output.getvalue().encode('utf-8')
    (directory / 'definition_answers_100.csv').write_bytes(raw)
    factory = sessions(f'sqlite:///{(ROOT / "acceptance.db").as_posix()}')
    with factory.begin() as session:
        key = 'definition-quiz-v1-' + hashlib.sha256(raw).hexdigest()
        batch = session.scalar(select(TaskUploadBatch).where(TaskUploadBatch.request_key == key))
        if batch is None:
            tid = IntakeService(session).save(dict(client_name='Vocabulary assessment',
                task_name='100 definitions - identify the word',
                required_capabilities='For each definition, identify the common English word or term it describes. The answer column is the candidate response, NOT a reference answer. Evaluate its semantic correctness directly from the definition. Reward 1 for a correct term or valid synonym; reward 0 for a term that does not match. Ignore capitalization. No hidden reference key is supplied.',
                outside_scope='Generating new candidate answers, external browsing, code execution.',
                quantities=[dict(category='Definition to word', minimum_tasks=100, attempts_per_task=1)],
                comments='Original authored demonstration data with intentionally mixed answer quality. Exactly two dataset columns: definition and answer. Each row is a separate task and recorded candidate attempt; answers were authored for this demonstration, not generated by a model.'))
            revision = session.scalar(select(ClientTaskRevision).where(ClientTaskRevision.task_id == tid))
            uploads = []
            for i, row in enumerate(rows, 1):
                buffer = io.StringIO(newline='')
                writer = csv.DictWriter(buffer, fieldnames=['definition', 'answer'])
                writer.writeheader(); writer.writerow(row)
                upload = io.BytesIO(buffer.getvalue().encode('utf-8'))
                upload.name = f'definition-{i:03d}.csv'
                uploads.append(upload)
            bid = save_csv_batch(session, revision.id, uploads, key)
        else:
            bid = batch.id
        jid = None
        summary = None
        if os.getenv('OPENAI_API_KEY'):
            from pipeline.llm_assessment import OpenAIJudge
            jid = run_agent(session, bid, adapter=OpenAIJudge(),
                progress=lambda done, total: print(f'Assessed {done}/{total}', flush=True))
            summary = session.get(AssessmentJob, jid).summary
    report = dict(rows=100, columns=['definition', 'answer'], batch_id=bid, assessment_id=jid,
        summary=summary, state='assessed' if jid else 'ready_for_llm',
        authored_answer_mix={'intended_correct': 75, 'intended_incorrect': 25},
        note='Answer mix describes construction, not LLM results. No reference words or rewards sent to the evaluator.')
    (directory / 'manifest.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
