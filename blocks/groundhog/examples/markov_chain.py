import numpy
import argparse
import logging
import pprint

import theano
from theano import tensor

from groundhog.mainLoop import MainLoop
from groundhog.trainer.SGD import SGD

from blocks.bricks import GatedRecurrent, Tanh
from blocks.select import Selector
from blocks.graph import ComputationGraph, Cost
from blocks.sequence_generators import (
    SequenceGenerator, LinearReadout, SoftmaxEmitter, LookupFeedback)
from blocks.initialization import Orthogonal, IsotropicGaussian, Constant
from blocks.groundhog import GroundhogIterator, GroundhogState, GroundhogModel
from blocks.serialization import load_params

floatX = theano.config.floatX

logger = logging.getLogger()


class ChainIterator(GroundhogIterator):
    """Training data generator."""

    num_states = 3
    trans_prob = numpy.array([[0.1, 0.5, 0.4],
                              [0.1, 0.9, 0.0],
                              [0.3, 0.3, 0.4]])
    values, vectors = numpy.linalg.eig(trans_prob.T)
    equilibrium = vectors[:, values.argmax()]
    equilibrium = equilibrium / equilibrium.sum()
    trans_entropy = trans_prob * numpy.log(trans_prob + 1e-6)
    entropy = equilibrium.dot(trans_entropy).sum()

    def __init__(self, rng, seq_len, batch_size):
        self.__dict__.update(**locals())
        del self.self

        logger.debug("Markov chain entropy: {}".format(self.entropy))
        logger.debug("Expected min error: {}".format(
            -self.entropy * self.seq_len * self.batch_size))

    def single_next(self):
        states = [0]
        while len(states) != self.seq_len:
            states.append(numpy.random.multinomial(
                1, self.trans_prob[states[-1]]).argmax())
        return states

    def next(self):
        """Generate random sequences from the family."""
        x = numpy.zeros((self.seq_len, self.batch_size), dtype='int64')
        for i in range(self.batch_size):
            x[:, i] = self.single_next()
        return dict(x=x)


def main():
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s: %(name)s: %(levelname)s: %(message)s")

    parser = argparse.ArgumentParser(
        "Case study of generating a Markov chain with RNN.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument(
        "mode", choices=["train", "sample"],
        help="The mode to run. Use `train` to train a new model"
             " and `sample` to sample a sequence generated by an"
             " existing one.")
    parser.add_argument(
        "prefix", default="sine",
        help="The prefix for model, timing and state files")
    parser.add_argument(
        "--steps", type=int, default=100,
        help="Number of steps to plot")
    args = parser.parse_args()

    dim = 10
    num_states = ChainIterator.num_states
    feedback_dim = 8

    transition = GatedRecurrent(
        name="transition", activation=Tanh(), dim=dim,
        weights_init=Orthogonal())
    generator = SequenceGenerator(
        LinearReadout(readout_dim=num_states, source_names=["states"],
                      emitter=SoftmaxEmitter(name="emitter"),
                      feedbacker=LookupFeedback(
                        num_states, feedback_dim, name='feedback'),
                      name="readout"),
        transition,
        weights_init=IsotropicGaussian(0.01), biases_init=Constant(0),
        name="generator")
    generator.allocate()
    logger.debug("Parameters:\n" +
                 pprint.pformat(
                    [(key, value.get_value().shape) for key, value
                     in Selector(generator).get_params().items()],
                    width=120))

    if args.mode == "train":
        rng = numpy.random.RandomState(1)
        batch_size = 50

        generator.initialize()
        cost = Cost(generator.cost(tensor.lmatrix('x')).sum())

        gh_model = GroundhogModel(generator, cost)
        state = GroundhogState(args.prefix, batch_size,
                               learning_rate=0.0001).as_dict()
        data = ChainIterator(rng, 100, batch_size)
        trainer = SGD(gh_model, state, data)
        main_loop = MainLoop(data, None, None, gh_model, trainer, state, None)
        main_loop.main()
    elif args.mode == "sample":
        load_params(generator,  args.prefix + "model.npz")

        sample = ComputationGraph(generator.generate(
            n_steps=args.steps, batch_size=1, iterate=True)).function()

        states, outputs, costs = [data[:, 0] for data in sample()]

        numpy.set_printoptions(precision=3, suppress=True)
        print "Generation cost:\n{}".format(costs.sum())

        freqs = numpy.bincount(outputs).astype(floatX)
        freqs /= freqs.sum()
        print "Frequencies:\n {} vs {}".format(freqs, ChainIterator.equilibrium)

        trans_freqs = numpy.zeros((num_states, num_states), dtype=floatX)
        for a, b in zip(outputs, outputs[1:]):
            trans_freqs[a, b] += 1
        trans_freqs /= trans_freqs.sum(axis=1)[:, None]
        print "Transition frequencies:\n{}\nvs\n{}".format(
            trans_freqs, ChainIterator.trans_prob)
    else:
        assert False


if __name__ == "__main__":
    main()
